# 六类附属文本翻译 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为表格、图片和图表的六类 caption/footnote 数组增加逐元素翻译、公式保护、断点恢复、Markdown 回退、统计和 Web 日志支持。

**Architecture:** 在 `translation.py` 中增加统一附属文本字段规格、逐元素状态数组和 `AuxiliaryTranslationOutcome`，复用正文翻译的公式保护、重试、并发和 checkpoint writer。`renderer.py` 只按原数组索引选择成功译文或原文；现有正文、表格对象状态和表格 HTML 翻译保持独立。统计格式化函数扩展附属文本分组，Web workflow 与 `TaskRunner` 自动复用。

**Tech Stack:** Python 3、dataclasses、`FormulaProtectionContext`、ThreadPoolExecutor、pytest、现有 Markdown renderer 和 Web workflow。

## Global Constraints

- 只处理 `table_caption`、`table_footnote`、`image_caption`、`image_footnote`、`chart_caption`、`chart_footnote`。
- 不处理 `ref_text`、`type=equation`、图片内部文字或现有 `table_body` 翻译逻辑。
- 保留 MinerU 原始字段；新增等长 `translated_*` 数组，失败/未完成位置使用 `null`。
- 每个位置的 `translation_status`、`translation_error` 必须与译文位置一致；单条失败不改变对象级任务状态。
- 公式保护必须覆盖 `$...$`、`$$...$$`、`\(...\)`、`\[...\]`，占位符被修改时仅该位置失败并回退原文。
- 原字段身份、数组长度、状态、译文和错误在断点恢复时严格校验；success 位置跳过，failed/pending 位置重试。
- 所有附属文本从 pending 转为 success/failed 后才结束；附属文本 failed 不使整个任务失败。
- `TranslationStats` 现有字段和 positional 构造保持兼容；`model_call_count` 增加附属文本模型调用数。
- 不修改 CLI 输出，不引入 VL/OCR，不重构无关翻译业务。
- 保留当前工作区已有 formula 相关修改和未跟踪计划文件，不修改这些文件。

---

### Task 1: Add failing coverage for auxiliary translation state and aggregation

**Files:**
- Modify: `tests/test_translation.py`

**Interfaces:**
- Consumes: `translate_content_list_file`, `TranslationStats`, existing `FakeTranslator`, `CellBatchTranslator`, `cell_response` and `read_items` helpers.
- Produces: failing tests defining the six-field state schema, per-position behavior, formula protection, resume validation, non-fatal failures and auxiliary statistics.

- [ ] **Step 1: Add a translator helper that records plain-text requests**

Add this helper after the existing translator helpers:

```python
class RecordingTranslator:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.received = []

    def translate(self, text, *, response_format=None):
        assert response_format is None
        self.received.append(text)
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response
```

- [ ] **Step 2: Add failing tests for all six fields and independent positions**

Add a test containing one table, one image and one chart. Each source field is a two-element string list; the translator returns responses in the order of the six fields. Assert every `translated_*` array has the same length, all successful positions are strings, and the raw fields remain unchanged:

```python
def test_translate_file_translates_all_six_auxiliary_fields_without_overwriting_source(
    tmp_path,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    items = [
        {
            "type": "table",
            "table_caption": ["TABLE 1 Requirements"],
            "table_footnote": ["Note A"],
            "table_body": "<table><tr><td>Alpha</td></tr></table>",
        },
        {
            "type": "image",
            "image_caption": ["Figure 1 Overview"],
            "image_footnote": ["Image note"],
        },
        {
            "type": "chart",
            "chart_caption": ["Chart 1 Results"],
            "chart_footnote": ["Chart note"],
        },
    ]
    source.write_text(json.dumps(items), encoding="utf-8")
    translator = RecordingTranslator([
        "表 1 要求",
        "注 A",
        "图 1 概览",
        "图片注释",
        "图表 1 结果",
        "图表注释",
    ])

    stats = translate_content_list_file(
        source, output, translator, max_retries=0, concurrency=1
    )

    result = read_items(output)
    assert result[0]["table_caption"] == items[0]["table_caption"]
    assert result[0]["translated_table_caption"] == ["表 1 要求"]
    assert result[0]["translated_table_footnote"] == ["注 A"]
    assert result[1]["translated_image_caption"] == ["图 1 概览"]
    assert result[1]["translated_image_footnote"] == ["图片注释"]
    assert result[2]["translated_chart_caption"] == ["图表 1 结果"]
    assert result[2]["translated_chart_footnote"] == ["图表注释"]
    assert all(
        entry["translation_status"] == "success"
        for item in result
        for entries in item.get("auxiliary_translation", {}).values()
        for entry in entries
    )
    assert stats.auxiliary_count == 6
    assert stats.auxiliary_success_count == 6
    assert stats.auxiliary_failed_count == 0
    assert stats.auxiliary_pending_count == 0
    assert stats.auxiliary_model_call_count == 6
    assert stats.model_call_count == 7
```

Add a second test where the middle table footnote fails:

```python
def test_table_footnote_failure_preserves_position_and_does_not_fail_table(
    tmp_path,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table = {
        "type": "table",
        "table_caption": ["Caption"],
        "table_footnote": ["First", "Middle", "Last"],
        "table_body": "<table><tr><td>Alpha</td></tr></table>",
    }
    source.write_text(json.dumps([table]), encoding="utf-8")
    translator = RecordingTranslator([
        "标题",
        "第一条",
        RuntimeError("footnote timeout"),
        "第三条",
    ])

    stats = translate_content_list_file(
        source, output, translator, max_retries=0, concurrency=1
    )

    result = read_items(output)[0]
    assert result["translated_table_footnote"] == ["第一条", None, "第三条"]
    assert result["auxiliary_translation"]["table_footnote"] == [
        {"translation_status": "success", "translation_error": None},
        {"translation_status": "failed", "translation_error": "footnote timeout"},
        {"translation_status": "success", "translation_error": None},
    ]
    assert result["translation_status"] == "success"
    assert stats.auxiliary_count == 4
    assert stats.auxiliary_success_count == 3
    assert stats.auxiliary_failed_count == 1
    assert stats.model_call_count == 4
```

- [ ] **Step 3: Add failing formula-protection tests for all four boundaries**

Use one caption containing `$a$`, `$$b$$`, `\(c\)`, and `\[d\]`. Assert the request contains no raw formulas, and the saved translation restores the exact formulas. Add a second response that removes one protected placeholder and assert only that caption position is `failed` with `null` translated value while the table continues successfully.

```python
def test_auxiliary_caption_protects_and_restores_all_formula_boundaries(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    caption = r"Values $a$, $$b$$, \(c\), and \[d\]."
    source.write_text(
        json.dumps([{"type": "image", "image_caption": [caption]}]),
        encoding="utf-8",
    )
    translator = RecordingTranslator(["值 ⟪PDFTRANS_FORMULA"])

    stats = translate_content_list_file(
        source, output, translator, max_retries=0, concurrency=1
    )

    request = translator.received[0]
    assert "$a$" not in request
    assert "$$b$$" not in request
    assert r"\(c\)" not in request
    assert r"\[d\]" not in request
    result = read_items(output)[0]
    assert result["translated_image_caption"] == [None]
    assert result["auxiliary_translation"]["image_caption"][0][
        "translation_status"
    ] == "failed"
    assert stats.auxiliary_failed_count == 1
```

Add the matching success test with a translator that edits only plain text:

```python
class FormulaPreservingTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text, *, response_format=None):
        assert response_format is None
        self.received.append(text)
        return text.replace("Values", "值")

# after translate_content_list_file(...):
result = read_items(output)[0]
assert result["translated_image_caption"][0] == (
    r"值 $a$, $$b$$, \(c\), and \[d\]."
)
```

- [ ] **Step 4: Add failing resume and identity-validation tests**

Cover all of the following with `translated_content_list.json` fixtures:

```python
def test_resume_skips_successful_auxiliary_positions_and_retries_failed(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    item = {"type": "image", "image_caption": ["A", "B", "C"]}
    source.write_text(json.dumps([item]), encoding="utf-8")
    output.write_text(json.dumps([{
        **item,
        "translated_image_caption": ["甲", None, "丙"],
        "auxiliary_translation": {
            "image_caption": [
                {"translation_status": "success", "translation_error": None},
                {"translation_status": "failed", "translation_error": "old"},
                {"translation_status": "success", "translation_error": None},
            ]
        },
    }]), encoding="utf-8")
    translator = RecordingTranslator(["乙"])

    stats = translate_content_list_file(
        source, output, translator, max_retries=0, concurrency=1
    )

    assert translator.received == ["B"]
    assert read_items(output)[0]["translated_image_caption"] == ["甲", "乙", "丙"]
    assert stats.skipped_auxiliary_success_count == 2
    assert stats.auxiliary_model_call_count == 1


@pytest.mark.parametrize("change", [
    lambda item: item["image_caption"].append("new"),
    lambda item: item["image_caption"].__setitem__(0, "changed"),
])
def test_resume_rejects_changed_auxiliary_source(change, tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    item = {"type": "image", "image_caption": ["A"]}
    source.write_text(json.dumps([item]), encoding="utf-8")
    output.write_text(json.dumps([{
        **item,
        "translated_image_caption": ["甲"],
        "auxiliary_translation": {
            "image_caption": [
                {"translation_status": "success", "translation_error": None}
            ]
        },
    }]), encoding="utf-8")
    changed = json.loads(json.dumps(item))
    change(changed)
    source.write_text(json.dumps([changed]), encoding="utf-8")

    with pytest.raises(TranslationContentError, match="身份字段"):
        translate_content_list_file(
            source, output, RecordingTranslator([]), max_retries=0, concurrency=1
        )
```

Also add malformed checkpoint cases for array length mismatch, success with `null`, failed with no error, pending with a non-null translation, and metadata/translated-field presence mismatch. Each must raise `TranslationContentError` before the translator is called.

- [ ] **Step 5: Add failing auxiliary-statistics and pending-guard tests**

Assert `TranslationStats` has `auxiliary_count`, `skipped_auxiliary_success_count`, `auxiliary_success_count`, `auxiliary_failed_count`, `auxiliary_pending_count`, and `auxiliary_model_call_count`. Monkeypatch `_collect_auxiliary_translation_work` to return an empty list while a prepared caption remains pending and assert `TranslationContentError` mentions `pending 附属文本`. Assert a failed caption alone returns normally and is visible in the final JSON.

- [ ] **Step 6: Run the translation tests to verify RED**

Run: `pytest -q tests/test_translation.py -k "auxiliary or caption or footnote"`

Expected: FAIL with missing auxiliary fields/state and missing `TranslationStats` attributes; existing正文/表格 tests are not changed by this RED step.

- [ ] **Step 7: Commit the failing translation tests**

```bash
git add tests/test_translation.py
git commit -m "test: specify auxiliary text translation behavior"
```

### Task 2: Implement auxiliary work items, checkpoint state, and statistics

**Files:**
- Modify: `src/pdf_trans/translation.py:56-129,172-359,362-644,647-882`
- Test: `tests/test_translation.py`

**Interfaces:**
- Consumes: existing `TextTranslator`, `FormulaProtectionContext`, `_translate_one`, `TranslationOutcome`, table outcomes, and checkpoint writer.
- Produces: `_AUXILIARY_FIELD_SPECS`, `AuxiliaryTranslationOutcome`, validated `auxiliary_translation` state, extended `TranslationStats`, and auxiliary-aware `format_translation_stats`.

- [ ] **Step 1: Define the six field specifications and per-entry outcome**

Add these constants near `_TABLE_METADATA_FIELDS`:

```python
_AUXILIARY_FIELD_SPECS = {
    "table": (
        ("table_caption", "translated_table_caption"),
        ("table_footnote", "translated_table_footnote"),
    ),
    "image": (
        ("image_caption", "translated_image_caption"),
        ("image_footnote", "translated_image_footnote"),
    ),
    "chart": (
        ("chart_caption", "translated_chart_caption"),
        ("chart_footnote", "translated_chart_footnote"),
    ),
}
_AUXILIARY_STATE_FIELD = "auxiliary_translation"
```

Add:

```python
@dataclass(frozen=True)
class AuxiliaryTranslationOutcome:
    index: int
    field: str
    item_index: int
    status: Literal["success", "failed"]
    translated_text: str | None
    error: str | None
    model_call_count: int
    elapsed_seconds: float
```

- [ ] **Step 2: Extend `TranslationStats` and the shared formatter**

Append these defaulted fields after the existing fields:

```python
    auxiliary_count: int = 0
    skipped_auxiliary_success_count: int = 0
    auxiliary_success_count: int = 0
    auxiliary_failed_count: int = 0
    auxiliary_pending_count: int = 0
    auxiliary_model_call_count: int = 0
```

Add an “附属文本翻译” block to `format_translation_stats` with total, skipped已有成功, success, failed, pending, and model calls. Change the final model-call line to `模型调用总数：N（正文 X，表格 Y，附属文本 Z）`.

- [ ] **Step 3: Prepare new items with position-preserving pending state**

Implement `_prepare_new_auxiliary_state(item)` and call it from `_prepare_new_items`. For every source field that is a list, write an equally long `[None, ...]` translated array and an equally long metadata array of `{"translation_status": "pending", "translation_error": None}`. Remove stale translated/state fields first. For a non-list source field, remove only the corresponding generated fields and do not create a task.

- [ ] **Step 4: Extend identity checking and restore checkpoint state**

Extend `_same_identity` to compare presence and exact value of the two source fields for the current `type`. Implement `_restore_auxiliary_state(item, old, index)` with these rules:

```python
if source is not a list:
    remove generated fields and return
if neither generated field nor state exists in old:
    initialize all positions as pending
elif only one exists:
    raise TranslationContentError("附属文本断点字段不完整")
else:
    require translated_values and states to be lists of len(source)
    require every state status/error/value combination to be valid
    copy both arrays into item
```

Change `_prepare_resumed_items` to return `(prepared, skipped_text_success, skipped_table_success, skipped_auxiliary_success)`. Count only `translation_status == "success"` entries in auxiliary state as skipped; retain the existing body/table restore behavior.

- [ ] **Step 5: Collect and translate auxiliary work items with existing formula protection**

Implement `_collect_auxiliary_translation_work(items)` returning tuples `(content_index, field, item_index, auxiliary_number, source_text)`. Iterate content objects in order, then field specs, then array indexes; skip entries whose state is success. Include failed and pending entries. For non-blank string values submit `_translate_one` so all four formula boundaries use the existing protection context. For invalid/non-string entries return an `AuxiliaryTranslationOutcome` with status failed, `translated_text=None`, a non-empty validation error, and zero calls.

Add `_translate_auxiliary_one(...)` to map the existing `TranslationOutcome` into `AuxiliaryTranslationOutcome` while preserving the content index, field name, and array index. Add `_apply_auxiliary_outcome` to update only that array position and its metadata object; never change the containing object's `translation_status`.

- [ ] **Step 6: Integrate auxiliary futures and final aggregation**

Extend the futures union to include `AuxiliaryTranslationOutcome`. Track `skipped_auxiliary_success`, `auxiliary_model_calls`, and add auxiliary futures to the existing executor. In the completion loop, apply auxiliary outcomes and include their model calls in `model_calls`.

After the existing table/text pending checks, count auxiliary metadata entries with a `Counter`. If `auxiliary_pending_count > 0`, raise `TranslationContentError("正式翻译结束后仍存在 pending 附属文本")`. Return the appended stats using final state counts and the separate skipped count:

```python
auxiliary_counts = Counter(
    entry["translation_status"]
    for item in items
    for entries in item.get(_AUXILIARY_STATE_FIELD, {}).values()
    for entry in entries
)
return TranslationStats(
    # existing text/table fields...
    auxiliary_count=auxiliary_counts.total(),
    skipped_auxiliary_success_count=skipped_auxiliary_success,
    auxiliary_success_count=auxiliary_counts.get("success", 0),
    auxiliary_failed_count=auxiliary_counts.get("failed", 0),
    auxiliary_pending_count=auxiliary_counts.get("pending", 0),
    auxiliary_model_call_count=auxiliary_model_calls,
)
```

- [ ] **Step 7: Run focused tests to verify GREEN**

Run: `pytest -q tests/test_translation.py -k "auxiliary or caption or footnote"`

Expected: all new auxiliary translation, formula, resume, identity, non-fatal failure, statistics and pending-guard tests pass. Then run `pytest -q tests/test_translation.py` and confirm the existing 40+正文/表格 tests remain green.

- [ ] **Step 8: Commit the translation implementation**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: translate auxiliary caption and footnote text"
```

### Task 3: Add failing renderer coverage and implement positional fallback rendering

**Files:**
- Modify: `tests/test_renderer.py`
- Modify: `src/pdf_trans/renderer.py:17-82`

**Interfaces:**
- Consumes: `translated_*` arrays and `auxiliary_translation` state produced by Task 2.
- Produces: `_render_auxiliary_field(item, source_field, translated_field, replacements) -> list[str]`, used by table/image/chart rendering.

- [ ] **Step 1: Add failing renderer tests**

Add a test with a table caption and footnote, an image caption/footnote, and a chart caption/footnote. Put `None` and failed states in the middle of arrays and assert the output uses translated values at successful positions and original values at failed positions, without changing order:

```python
def test_render_items_selects_auxiliary_translation_per_position():
    items = [
        {
            "type": "table",
            "table_caption": ["Table original"],
            "translated_table_caption": ["表格译文"],
            "auxiliary_translation": {
                "table_caption": [
                    {"translation_status": "success", "translation_error": None}
                ],
                "table_footnote": [
                    {"translation_status": "success", "translation_error": None},
                    {"translation_status": "failed", "translation_error": "timeout"},
                ],
            },
            "table_footnote": ["注释译文", "注释原文"],
            "translated_table_footnote": ["注释译文", None],
            "table_body": "<table><tr><td>Body</td></tr></table>",
        },
        {
            "type": "image",
            "img_path": "images/a.png",
            "image_caption": ["Caption A", "Caption B"],
            "translated_image_caption": ["图注 A", None],
            "image_footnote": ["Footnote"],
            "translated_image_footnote": [None],
            "auxiliary_translation": {
                "image_caption": [
                    {"translation_status": "success", "translation_error": None},
                    {"translation_status": "failed", "translation_error": "timeout"},
                ],
                "image_footnote": [
                    {"translation_status": "failed", "translation_error": "timeout"}
                ],
            },
        },
    ]

    assert render_items(items) == (
        "表格译文\n\n"
        "<table><tr><td>Body</td></tr></table>\n\n"
        "注释译文\n\n"
        "注释原文\n\n"
        "![](images/a.png)\n\n"
        "图注 A\n\n"
        "Caption B\n\n"
        "Footnote\n"
    )
```

Add a test proving a malformed/missing auxiliary state falls back to every source element and a test proving caption/footnote formulas still pass through the existing `formula_audit` replacement map.

- [ ] **Step 2: Run renderer tests to verify RED**

Run: `pytest -q tests/test_renderer.py -k "auxiliary or positional"`

Expected: FAIL because renderer currently reads only original caption/footnote arrays.

- [ ] **Step 3: Implement `_render_auxiliary_field`**

Implement the helper with these rules:

```python
def _render_auxiliary_field(
    item, source_field, translated_field, replacements
):
    source_values = item.get(source_field)
    if not isinstance(source_values, list):
        return []
    translated_values = item.get(translated_field)
    states = item.get("auxiliary_translation", {}).get(source_field)
    if not isinstance(translated_values, list) or len(translated_values) != len(source_values):
        translated_values = []
    if not isinstance(states, list) or len(states) != len(source_values):
        states = []
    rendered = []
    for index, source in enumerate(source_values):
        if not _is_non_blank_string(source):
            continue
        value = source
        if (
            index < len(states)
            and states[index].get("translation_status") == "success"
            and index < len(translated_values)
            and _is_non_blank_string(translated_values[index])
        ):
            value = translated_values[index]
        if replacements:
            value = replace_formula_spans(value, replacements)
        rendered.append(value)
    return rendered
```

Use it in `_render_item`: image/chart append image, caption helper, footnote helper; table append caption helper, selected table body, footnote helper. Do not change `ref_text` or `equation` branches.

- [ ] **Step 4: Run renderer and regression tests**

Run: `pytest -q tests/test_renderer.py tests/web/test_markdown.py`

Expected: all renderer tests pass, including existing source-array behavior and new per-position fallback behavior.

- [ ] **Step 5: Commit renderer support**

```bash
git add src/pdf_trans/renderer.py tests/test_renderer.py
git commit -m "feat: render translated auxiliary text by position"
```

### Task 4: Wire auxiliary statistics into Web logs and summaries

**Files:**
- Modify: `src/pdf_trans/translation.py:76-100,740-777`
- Modify: `tests/test_workflow.py:560-620,720-730`
- Modify: `tests/web/test_task_runner.py:78-200`

**Interfaces:**
- Consumes: auxiliary-aware `TranslationStats` and `format_translation_stats` from Task 2.
- Produces: Web stage log and TaskRunner summary sections explicitly showing attached-text counts and calls.

- [ ] **Step 1: Add failing log and summary assertions**

Populate a `TranslationStats` fixture with `auxiliary_count=6`, `skipped_auxiliary_success_count=2`, `auxiliary_success_count=5`, `auxiliary_failed_count=1`, `auxiliary_pending_count=0`, and `auxiliary_model_call_count=4`. Assert workflow logs and both resumed/full TaskRunner summaries contain:

```text
附属文本翻译：
  总数：6
  跳过已有成功：2
  成功：5
  失败：1
  pending：0
  模型调用：4
模型调用总数：...
```

Run: `pytest -q tests/test_workflow.py tests/web/test_task_runner.py -k "translation or runner"`

Expected: FAIL because the formatter currently stops after table statistics and only shows a two-way model-call split.

- [ ] **Step 2: Implement formatter and checkpoint-stage log updates**

Append the auxiliary block to `format_translation_stats`. Change the checkpoint preparation result to include auxiliary total and skipped counts, and change the “准备并发翻译” message to include the number of auxiliary work items. Keep the existing Web workflow stage names and TaskRunner code paths; they already reuse the formatter.

- [ ] **Step 3: Run Web-focused tests to verify GREEN**

Run: `pytest -q tests/test_workflow.py tests/web/test_task_runner.py tests/web/test_worker.py`

Expected: all workflow, TaskRunner and worker tests pass, with failed auxiliary positions visible but no task failure caused solely by those failures.

- [ ] **Step 4: Commit Web statistics integration**

```bash
git add src/pdf_trans/translation.py tests/test_workflow.py tests/web/test_task_runner.py
git commit -m "feat: include auxiliary text in web translation summaries"
```

### Task 5: Verify complete regression and scope

**Files:**
- Verify: `src/pdf_trans/translation.py`
- Verify: `src/pdf_trans/renderer.py`
- Verify: `src/pdf_trans/workflow.py`
- Verify: `src/pdf_trans/web/task_runner.py`
- Verify: `tests/test_translation.py`
- Verify: `tests/test_renderer.py`
- Verify: `tests/test_workflow.py`
- Verify: `tests/web/test_task_runner.py`

**Interfaces:**
- Consumes: all implementation and tests from Tasks 1–4.
- Produces: fresh test evidence and a requirement-by-requirement scope check.

- [ ] **Step 1: Run focused translation and renderer tests**

Run:

```bash
pytest -q tests/test_translation.py tests/test_renderer.py tests/test_table_translation.py
```

Expected: exit code 0 and zero failures; existing body and table-cell formula behavior remains green.

- [ ] **Step 2: Run the complete test suite**

Run: `pytest -q`

Expected: exit code 0 and zero failures. Existing CLI tests must continue to pass without changing CLI output.

- [ ] **Step 3: Check diff scope and whitespace**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: no whitespace errors; only auxiliary translation/renderer/Web tests and this task's docs are changed. Existing formula-scanner edits and unrelated untracked plan remain untouched.

- [ ] **Step 4: Re-read requirements against implementation**

Verify six raw fields are never overwritten; all six translated fields preserve array positions; per-position status/error validation is strict; all four formula boundaries use `FormulaProtectionContext`; failed auxiliary entries do not alter object-level table/image/chart status; pending still raises; renderer order is unchanged; attached-text totals, skipped successes, final statuses and model calls appear in Web logs; `ref_text`, equations, image internals and table body remain out of scope.
