# Web 翻译统计与任务摘要 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将正文和 HTML 表格的最终翻译统计统一纳入 `TranslationStats`，并让 Web 阶段日志和任务摘要清楚展示两类统计及模型调用口径。

**Architecture:** 在 `translation.py` 中扩展兼容的统计数据结构，在现有断点恢复、结果应用和最终收尾位置分别累计正文/表格状态、跳过数、单元格元数据和模型调用数。由同一格式化函数生成多行正文/表格摘要，`workflow.py` 和 Web `TaskRunner` 复用它，避免各处自行拼接导致口径漂移。

**Tech Stack:** Python 3、dataclasses、pytest、现有 `logged_stage` 和 Web `TaskRunner`。

## Global Constraints

- 保留现有正文统计字段及顺序，新增字段追加默认值，兼容已有 positional 构造。
- `model_call_count` 是正文和表格模型调用数之和；Web 日志必须明确这一点。
- `translation_status=success` 且 `table_translation_partial=true` 的表格同时计入表格成功和部分成功。
- 表格 pending 和正文 pending 都继续触发现有翻译结束异常；部分成功表格不触发异常。
- 不修改表格翻译、重试、单元格回退以及任务 succeeded/failed 判定逻辑。
- 不修改 CLI 输出；只更新翻译统计核心和 Web 阶段日志/任务摘要。
- 避开当前工作区已有的 `src/pdf_trans/formula_scanner.py`、相关 formula 测试和未跟踪的其他计划文件。

---

### Task 1: Add failing coverage for complete translation statistics

**Files:**
- Modify: `tests/test_translation.py`

**Interfaces:**
- Consumes: existing `translate_content_list_file`, `JsonTableTranslator`, `SelectiveTranslator`, `CellBatchTranslator`, `cell_response`, and `TranslationStats` helpers.
- Produces: regression tests that define the appended `TranslationStats` fields and the exact aggregation semantics implemented in `translation.py`.

- [ ] **Step 1: Write the failing tests**

Add assertions to the existing table tests and add the following focused cases near the current table statistics tests:

```python
def test_stats_aggregate_text_and_table_successes_and_model_calls(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td><td>Beta</td></tr></table>"
    source.write_text(
        json.dumps([
            {"type": "text", "text": "Paragraph"},
            {"type": "table", "table_body": table_body},
        ]),
        encoding="utf-8",
    )

    class Translator:
        def translate(self, text, *, response_format=None):
            if response_format is None:
                return "正文译文"
            payload = json.loads(text)
            return cell_response(*[
                (cell["cell_id"], cell["text"] + "译文")
                for cell in payload["cells"]
            ])

    stats = translate_content_list_file(
        source, output, Translator(), max_retries=0, concurrency=1
    )

    assert stats.text_count == 1
    assert stats.success_count == 1
    assert stats.failed_count == 0
    assert stats.pending_count == 0
    assert stats.table_count == 1
    assert stats.table_success_count == 1
    assert stats.table_failed_count == 0
    assert stats.table_pending_count == 0
    assert stats.table_partial_success_count == 0
    assert stats.table_translation_success_cell_count == 2
    assert stats.table_translation_fallback_cell_count == 0
    assert stats.model_call_count == 2
    assert stats.text_model_call_count == 1
    assert stats.table_model_call_count == 1


def test_stats_count_failed_table_without_converting_it_to_text_failure(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{
            "type": "table",
            "table_body": "<table><tr><td>broken</tr></table>",
        }]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source, output, FakeTranslator([]), max_retries=0, concurrency=1
    )

    assert stats.text_count == 0
    assert stats.success_count == 0
    assert stats.failed_count == 0
    assert stats.table_count == 1
    assert stats.table_success_count == 0
    assert stats.table_failed_count == 1
    assert stats.table_pending_count == 0
    assert stats.model_call_count == 0


def test_stats_sum_partial_table_fallbacks_across_multiple_tables(
    tmp_path,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_bodies = [
        "<table><tr><td>Alpha</td><td>Beta</td></tr></table>",
        "<table><tr><td>Gamma</td><td>Delta</td></tr></table>",
    ]
    source.write_text(
        json.dumps([{"type": "table", "table_body": body} for body in table_bodies]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        cells = payload["cells"]
        if call_number == 1:
            return cell_response((cells[0]["cell_id"], "已译"))
        return cell_response(*[
            (cell["cell_id"], cell["text"] + "译文") for cell in cells
        ])

    stats = translate_content_list_file(
        source,
        output,
        CellBatchTranslator(respond),
        max_retries=0,
        concurrency=1,
    )

    assert stats.table_count == 2
    assert stats.table_success_count == 2
    assert stats.table_partial_success_count == 1
    assert stats.table_failed_count == 0
    assert stats.table_translation_success_cell_count == 3
    assert stats.table_translation_fallback_cell_count == 1


def test_resume_counts_skipped_successful_text_and_table_separately(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    normalized = [
        {"type": "text", "text": "正文"},
        {"type": "table", "table_body": table_body},
    ]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(json.dumps([
        {
            **normalized[0],
            "translated_text": "旧正文",
            "translation_status": "success",
        },
        {
            **normalized[1],
            "translated_table_body": table_body,
            "translation_status": "success",
        },
    ]), encoding="utf-8")

    stats = translate_content_list_file(
        source, output, FakeTranslator([]), max_retries=0, concurrency=1
    )

    assert stats.skipped_success_count == 1
    assert stats.skipped_table_success_count == 1
    assert stats.success_count == 1
    assert stats.table_success_count == 1
    assert stats.model_call_count == 0


def test_pending_table_still_raises_after_translation(tmp_path, monkeypatch):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{
            "type": "table",
            "table_body": "<table><tr><td>Alpha</td></tr></table>",
        }]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pdf_trans.translation._collect_table_translation_work",
        lambda items: [],
    )

    with pytest.raises(TranslationContentError, match="pending 表格"):
        translate_content_list_file(
            source, output, FakeTranslator([]), max_retries=0, concurrency=1
        )
```

Update the existing `TranslationStats(...)` equality expectations for table cases to include the new table counts and split model-call counts. Leave existing text-only test inputs unchanged so the original正文统计 behavior remains covered.

- [ ] **Step 2: Run the focused tests to verify they fail for the missing statistics**

Run: `pytest -q tests/test_translation.py -k "stats_aggregate or failed_table or partial_table_fallbacks or resume_counts_skipped or pending_table"`

Expected: FAIL with missing `TranslationStats` attributes and/or the old single skip-count return shape; no production code is changed in this step.

- [ ] **Step 3: Commit the red tests**

```bash
git add tests/test_translation.py
git commit -m "test: specify web translation statistics aggregation"
```

### Task 2: Implement compatible statistics aggregation and formatting

**Files:**
- Modify: `src/pdf_trans/translation.py:56-64,267-319,680-799`
- Test: `tests/test_translation.py`

**Interfaces:**
- Consumes: existing translation outcomes and persisted table metadata.
- Produces: `TranslationStats` with appended fields and `format_translation_stats(stats: TranslationStats) -> str` for Web workflow consumers.

- [ ] **Step 1: Extend `TranslationStats` without changing existing field order**

Append these fields with `= 0` after `pending_count`:

```python
    table_count: int = 0
    table_success_count: int = 0
    table_failed_count: int = 0
    table_pending_count: int = 0
    table_partial_success_count: int = 0
    table_translation_success_cell_count: int = 0
    table_translation_fallback_cell_count: int = 0
    skipped_table_success_count: int = 0
    text_model_call_count: int = 0
    table_model_call_count: int = 0
```

- [ ] **Step 2: Return separate resume skip counts**

Change `_prepare_resumed_items` to return `(prepared, skipped_text_success, skipped_table_success)`. Increment `skipped_table_success` in the table branch when `_restore_table_state` sees `success`; do not count pending or failed tables.

- [ ] **Step 3: Accumulate status and model-call counts by object type**

Keep `model_calls` as the sum of every outcome. Add `text_model_calls` and `table_model_calls`, incrementing them based on `isinstance(outcome, TableTranslationOutcome)`. After all outcomes, use two `Counter`s over `items`:

```python
text_counts = Counter(
    item.get("translation_status")
    for item in items
    if item.get("type") == "text"
)
table_counts = Counter(
    item.get("translation_status")
    for item in items
    if item.get("type") == "table"
)
table_successes = [
    item for item in items
    if item.get("type") == "table"
    and item.get("translation_status") == "success"
]
```

Return text fields from `text_counts`, table status fields from `table_counts`, and sum table cell fields from `table_successes`. Count partial tables only where `table_translation_partial is True`. Preserve both pending guards before returning.

- [ ] **Step 4: Add the shared Web summary formatter**

Implement `format_translation_stats` beside `TranslationStats` with this stable shape:

```text
正文翻译：
  总数：...
  跳过已有成功：...
  成功：...
  失败：...
  pending：...
表格翻译：
  总数：...
  跳过已有成功：...
  成功：...
  其中部分成功：...
  失败：...
  pending：...
  成功单元格：...
  回退原文单元格：...
模型调用总数：...（正文 ...，表格 ...）
```

- [ ] **Step 5: Run the focused translation tests to verify green**

Run: `pytest -q tests/test_translation.py -k "stats_aggregate or failed_table or partial_table_fallbacks or resume_counts_skipped or pending_table"`

Expected: all new aggregation tests pass. Then run `pytest -q tests/test_translation.py` and update only expected statistics values if the full module identifies stale table assertions; all tests must pass before moving on.

- [ ] **Step 6: Commit the statistics implementation**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: aggregate body and table translation statistics"
```

### Task 3: Wire the shared summary into Web workflow logs and task summaries

**Files:**
- Modify: `src/pdf_trans/workflow.py:31-35,156-184,342-357`
- Modify: `src/pdf_trans/web/task_runner.py:1-18,90-136`
- Modify: `tests/test_workflow.py:631-672`
- Modify: `tests/web/test_task_runner.py:78-166`

**Interfaces:**
- Consumes: `format_translation_stats` and the expanded `TranslationStats`.
- Produces: Web stage logs and `TaskArtifacts.summary` with separate正文/表格 sections.

- [ ] **Step 1: Add failing workflow log assertions**

In `tests/test_workflow.py`, add a focused stage-log test that bypasses the model and supplies a populated result:

```python
def test_process_translation_file_logs_body_and_table_statistics(
    tmp_path,
    monkeypatch,
    caplog,
):
    normalized = tmp_path / "normalized_content_list.json"
    translated = tmp_path / "translated_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    stats = TranslationStats(
        1, 4, 1, 1, 0, 0,
        table_count=3,
        table_success_count=2,
        table_failed_count=1,
        table_partial_success_count=1,
        table_translation_success_cell_count=5,
        table_translation_fallback_cell_count=2,
        skipped_table_success_count=1,
        text_model_call_count=1,
        table_model_call_count=3,
    )
    monkeypatch.setattr(
        "pdf_trans.workflow._run_translation",
        lambda path, **kwargs: TranslationFileResult(
            normalized_path=path,
            translated_path=translated,
            stats=stats,
        ),
    )
    caplog.set_level(logging.INFO)

    process_translation_file(normalized)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "正文翻译：" in messages
    assert "表格翻译：" in messages
    assert "其中部分成功：1" in messages
    assert "成功单元格：5" in messages
    assert "回退原文单元格：2" in messages
    assert "模型调用总数：4（正文 1，表格 3）" in messages
```

Import `TranslationFileResult` in that test module. The existing full-workflow stage test should continue to assert the stage start/end names, and should additionally assert the two group labels when its translation result is text-only.

In `tests/web/test_task_runner.py`, import `TranslationStats`, change `fake_translation_result` to return `TranslationStats(1, 4, 1, 1, 0, 0, table_count=2, table_success_count=1, table_failed_count=1, table_partial_success_count=1, table_translation_success_cell_count=3, table_translation_fallback_cell_count=1, skipped_table_success_count=1, text_model_call_count=2, table_model_call_count=2)`, and use the same populated stats in the full-workflow fake. Add these assertions after each `runner.run(...)`:

```python
assert "正文翻译：" in result.summary
assert "表格翻译：" in result.summary
assert "其中部分成功：1" in result.summary
assert "成功单元格：3" in result.summary
assert "回退原文单元格：1" in result.summary
assert "模型调用总数：4（正文 2，表格 2）" in result.summary
```

Run: `pytest -q tests/test_workflow.py::test_process_pdf_logs_stage_actions_counts_and_cross_page_merge tests/web/test_task_runner.py -k "runner"`

Expected: FAIL because the current stage results and TaskRunner summaries only mention正文 success/failed counts.

- [ ] **Step 2: Use the formatter in `process_translation_file` and full PDF workflow**

Import `format_translation_stats` in `workflow.py`. Replace the single-line stage result in both translation stages with:

```python
translation_stage.set_result(format_translation_stats(result.stats))
```

and:

```python
stage.set_result(format_translation_stats(translation_result.stats))
```

Keep the existing stage names and rendering flow so task success behavior is unchanged.

- [ ] **Step 3: Use the formatter in `TaskRunner` summaries**

Import the formatter in `web/task_runner.py` and build summaries as:

```python
summary = (
    "断点续传完成：\n" + format_translation_stats(result.stats)
)
```

and:

```python
summary = (
    f"完整工作流完成：输入 {result.before_count} 项，"
    f"过滤 {result.filtered_count} 项，"
    f"跨页候选 {result.candidate_count} 个\n"
    + format_translation_stats(result.translation_stats)
)
```

Do not add task-state checks or change the existing exception paths.

- [ ] **Step 4: Run Web-focused tests to verify green**

Run: `pytest -q tests/test_workflow.py tests/web/test_task_runner.py`

Expected: all workflow and TaskRunner tests pass, including the new assertions that Web stage results and final summaries expose table statistics.

- [ ] **Step 5: Commit the Web logging integration**

```bash
git add src/pdf_trans/workflow.py src/pdf_trans/web/task_runner.py tests/test_workflow.py tests/web/test_task_runner.py
git commit -m "feat: show table statistics in web translation summaries"
```

### Task 4: Verify compatibility and requirement coverage

**Files:**
- Verify: `src/pdf_trans/translation.py`
- Verify: `src/pdf_trans/workflow.py`
- Verify: `src/pdf_trans/web/task_runner.py`
- Verify: `tests/test_translation.py`
- Verify: `tests/test_workflow.py`
- Verify: `tests/web/test_task_runner.py`

**Interfaces:**
- Consumes: all changes from Tasks 1–3.
- Produces: fresh test evidence and a requirement-by-requirement check before completion.

- [ ] **Step 1: Run all relevant tests**

Run:

```bash
pytest -q tests/test_translation.py tests/test_workflow.py tests/web/test_task_runner.py tests/web/test_worker.py
```

Expected: exit code 0 and zero failures.

- [ ] **Step 2: Run the complete test suite**

Run: `pytest -q`

Expected: exit code 0 and zero failures. Existing CLI tests may continue to use the old six positional `TranslationStats` constructor; the appended defaults must keep those tests passing without changing CLI output.

- [ ] **Step 3: Inspect the final diff and verify scope**

Run:

```bash
git diff --check HEAD~3..HEAD
git status --short
git diff --stat HEAD~3..HEAD
```

Expected: no whitespace errors; only the statistics core, Web workflow/TaskRunner, their tests, and this task's commits are included. Existing formula-scanner changes and unrelated plans remain untouched.

- [ ] **Step 4: Re-read the requirements against the implementation**

Confirm in code and tests that table total/success/failed/pending/partial counts, successful/fallback cell totals, separate resume skip counts, total model calls, pending guards, and Web stage/final summaries are all present. Confirm no CLI file changed and no task success判定 code changed.
