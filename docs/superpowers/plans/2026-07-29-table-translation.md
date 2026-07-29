# Table Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate eligible English text nodes inside `type="table"` HTML in one ID-addressed model call per table, while preserving HTML and falling back safely on every table-specific failure.

**Architecture:** Add a pure `table_translation` module based on the standard-library `HTMLParser`. It records source spans for eligible text nodes and replaces only those spans, so tags and attributes are never serialized or rewritten. Extend the existing translation orchestrator with a separate table outcome path that reuses `TextTranslator.translate()`, retries, concurrency, checkpoints, and logging without changing paragraph translation behavior.

**Tech Stack:** Python 3.11, standard-library `html`, `html.parser`, `json`, `dataclasses`, existing `TextTranslator`, pytest.

## Global Constraints

- Existing `type="text"` requests, status fields, retries, ordering, text-only
  counts, and rendering must remain unchanged. The existing
  `model_call_count` additionally counts table batch calls.
- Table HTML is read from `table_body`; the original field is never overwritten.
- Successful translated HTML is stored in `translated_table_body`.
- Only visible text nodes under `td`, `th`, or HTML `caption` are eligible.
- Pure-number, punctuation-only, non-English, and whitespace-only nodes are not sent.
- Mixed English/numeric nodes such as `Density at 20 °C` are sent as one node.
- Every eligible node receives a stable traversal-order ID.
- One table produces at most one model call per retry attempt.
- Response application is by ID, never by array position.
- Any table parse, model, response, ID, count, or HTML validation failure preserves the original HTML, records a failed status and log, checkpoints the result, and continues the document.
- No formula-aware splitting, table-context prompting, caption-array translation, footnote translation, or cross-table batching is added.
- Do not add a new runtime dependency.

---

## File Structure

- Create `src/pdf_trans/table_translation.py`: HTML parsing, stable-node extraction, JSON batch request/response handling, escaped span replacement, and structural validation.
- Create `tests/test_table_translation.py`: focused unit tests for extraction, ID-based fill, preservation, and validation errors.
- Modify `src/pdf_trans/translation.py`: table state/resume handling, table work collection, model retries, table outcomes, fallback logging, and checkpoint application.
- Modify `tests/test_translation.py`: end-to-end table translation, numeric skip, failure fallback, resume, and text-regression tests.
- Modify `src/pdf_trans/renderer.py`: select validated translated table HTML on success.
- Modify `tests/test_renderer.py`: translated-table selection and fallback tests.

---

### Task 1: Pure HTML Table Translation Plan

**Files:**
- Create: `tests/test_table_translation.py`
- Create: `src/pdf_trans/table_translation.py`

**Interfaces:**
- Consumes: a non-blank HTML `str` from `table_body`.
- Produces:
  - `TableTranslationError(ValueError)`;
  - `TableTextNode(node_id: str, text: str, start: int, end: int)`;
  - `PreparedTableTranslation(original_html: str, nodes: tuple[TableTextNode, ...], structure: tuple[tuple[object, ...], ...])`;
  - `prepare_table_translation(table_html: str) -> PreparedTableTranslation`;
  - `PreparedTableTranslation.build_request() -> str`;
  - `PreparedTableTranslation.apply_response(response: str) -> str`.

- [ ] **Step 1: Write failing extraction and preservation tests**

Create `tests/test_table_translation.py`:

```python
import json

import pytest

from pdf_trans.table_translation import (
    TableTranslationError,
    prepare_table_translation,
)


def response(*items):
    return json.dumps(
        {
            "translations": [
                {"id": node_id, "text": text}
                for node_id, text in items
            ]
        },
        ensure_ascii=False,
    )


def test_extracts_caption_th_and_td_but_skips_numeric_and_hidden_text():
    prepared = prepare_table_translation(
        "<table><caption>Operating Data</caption>"
        "<tr><th>Component</th><th>123</th></tr>"
        "<tr><td>  Ethanol  </td><td><script>Ignored</script>42</td></tr>"
        "</table>"
    )

    payload = json.loads(prepared.build_request())

    assert payload["items"] == [
        {"id": "table-text-0001", "text": "Operating Data"},
        {"id": "table-text-0002", "text": "Component"},
        {"id": "table-text-0003", "text": "Ethanol"},
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "<table" not in serialized
    assert "123" not in serialized
    assert "42" not in serialized
    assert "Ignored" not in serialized


def test_applies_reordered_ids_and_preserves_tags_attributes_and_whitespace():
    source = (
        '<table class="source"><tr>'
        '<th rowspan="2"> Component </th>'
        '<td colspan="3"><em>Mass</em> Fraction</td>'
        '<td data-code="A1">100</td>'
        "</tr></table>"
    )
    prepared = prepare_table_translation(source)
    request = prepared.build_request()

    translated = prepared.apply_response(
        response(
            ("table-text-0003", "分数"),
            ("table-text-0001", "组分"),
            ("table-text-0002", "质量"),
        )
    )

    assert translated == (
        '<table class="source"><tr>'
        '<th rowspan="2"> 组分 </th>'
        '<td colspan="3"><em>质量</em> 分数</td>'
        '<td data-code="A1">100</td>'
        "</tr></table>"
    )
    assert 'rowspan="2"' in translated
    assert 'colspan="3"' in translated
    assert 'data-code="A1"' in translated
    assert "rowspan" not in request
    assert "colspan" not in request
    assert "data-code" not in request
    assert "A1" not in request


def test_escapes_translated_text_without_changing_structure():
    prepared = prepare_table_translation(
        "<table><tr><td>Salt and water</td></tr></table>"
    )

    translated = prepared.apply_response(
        response(("table-text-0001", "盐 < 水 & 乙醇"))
    )

    assert translated == (
        "<table><tr><td>盐 &lt; 水 &amp; 乙醇</td></tr></table>"
    )
```

- [ ] **Step 2: Run the extraction tests and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_table_translation.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'pdf_trans.table_translation'`.

- [ ] **Step 3: Add failing validation tests**

Append to `tests/test_table_translation.py`:

```python
@pytest.mark.parametrize(
    ("raw_response", "message"),
    [
        ("not json", "响应不是有效 JSON"),
        (
            json.dumps({"translations": []}),
            "结果数量不一致",
        ),
        (
            response(("table-text-9999", "错误节点")),
            "ID 集合不一致",
        ),
        (
            response(
                ("table-text-0001", "甲"),
                ("table-text-0001", "乙"),
            ),
            "存在重复 ID",
        ),
        (
            json.dumps(
                {
                    "translations": [
                        {"id": "table-text-0001", "text": "   "}
                    ]
                }
            ),
            "译文不能为空",
        ),
    ],
)
def test_rejects_invalid_batch_responses(raw_response, message):
    prepared = prepare_table_translation(
        "<table><tr><td>Alpha</td></tr></table>"
    )

    with pytest.raises(TableTranslationError, match=message):
        prepared.apply_response(raw_response)


@pytest.mark.parametrize(
    "source",
    [
        "",
        "<div>not a table</div>",
        "<table><tr><td>Alpha</tr></table>",
        "<table><tr><td>Alpha</td></tr>",
    ],
)
def test_rejects_blank_non_table_or_unbalanced_html(source):
    with pytest.raises(TableTranslationError, match="HTML"):
        prepare_table_translation(source)


def test_table_with_no_eligible_text_builds_no_model_payload():
    prepared = prepare_table_translation(
        "<table><tr><td>123.45</td><td>--</td><td>中文</td></tr></table>"
    )

    assert prepared.nodes == ()
    with pytest.raises(TableTranslationError, match="没有待翻译节点"):
        prepared.build_request()
```

Run:

```bash
.venv/bin/pytest tests/test_table_translation.py -v
```

Expected: the same import failure; all desired public behavior is now specified before production code exists.

- [ ] **Step 4: Implement the minimal parser and response mapper**

Create `src/pdf_trans/table_translation.py`:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Any


_TARGET_TAGS = frozenset({"td", "th", "caption"})
_HIDDEN_TAGS = frozenset({"script", "style", "template"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_ENGLISH_RE = re.compile(r"[A-Za-z]")


class TableTranslationError(ValueError):
    """One table cannot be translated or safely reconstructed."""


@dataclass(frozen=True)
class TableTextNode:
    node_id: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class PreparedTableTranslation:
    original_html: str
    nodes: tuple[TableTextNode, ...]
    structure: tuple[tuple[object, ...], ...]

    def build_request(self) -> str:
        if not self.nodes:
            raise TableTranslationError("表格没有待翻译节点")
        return json.dumps(
            {
                "task": (
                    "Translate every item text from English to Simplified "
                    "Chinese. Return JSON only with the same IDs and shape "
                    '{"translations":[{"id":"...","text":"..."}]}.'
                ),
                "items": [
                    {"id": node.node_id, "text": node.text}
                    for node in self.nodes
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def apply_response(self, response: str) -> str:
        translations = _parse_response(
            response,
            tuple(node.node_id for node in self.nodes),
        )
        translated = self.original_html
        for node in reversed(self.nodes):
            translated = (
                translated[: node.start]
                + escape(translations[node.node_id], quote=False)
                + translated[node.end :]
            )
        translated_structure = _parse_html(translated, collect_nodes=False)
        if translated_structure.structure != self.structure:
            raise TableTranslationError("翻译后的 HTML 结构校验失败")
        return translated


@dataclass(frozen=True)
class _ParsedHTML:
    nodes: tuple[TableTextNode, ...]
    structure: tuple[tuple[object, ...], ...]


class _TableHTMLParser(HTMLParser):
    def __init__(self, source: str, *, collect_nodes: bool) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._collect_nodes = collect_nodes
        self._line_offsets = [0]
        for match in re.finditer(r"\n", source):
            self._line_offsets.append(match.end())
        self._stack: list[str] = []
        self._nodes: list[TableTextNode] = []
        self._structure: list[tuple[object, ...]] = []
        self._saw_table = False

    @property
    def result(self) -> _ParsedHTML:
        if self._stack:
            raise TableTranslationError(
                "HTML 标签未闭合：" + ", ".join(self._stack)
            )
        if not self._saw_table:
            raise TableTranslationError("HTML 中缺少 table 标签")
        return _ParsedHTML(tuple(self._nodes), tuple(self._structure))

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._structure.append(("start", tag, tuple(attrs)))
        if tag == "table":
            self._saw_table = True
        if tag not in _VOID_TAGS:
            self._stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._structure.append(("self", tag, tuple(attrs)))
        if tag == "table":
            self._saw_table = True

    def handle_endtag(self, tag: str) -> None:
        if not self._stack or self._stack[-1] != tag:
            expected = self._stack[-1] if self._stack else "无"
            raise TableTranslationError(
                f"HTML 结束标签不匹配：期望 {expected}，实际 {tag}"
            )
        self._stack.pop()
        self._structure.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if not self._collect_nodes:
            return
        if "table" not in self._stack:
            return
        if not any(tag in _TARGET_TAGS for tag in self._stack):
            return
        if any(tag in _HIDDEN_TAGS for tag in self._stack):
            return
        stripped = data.strip()
        if not stripped or _ENGLISH_RE.search(stripped) is None:
            return
        data_start = self._absolute_position()
        content_start = data_start + len(data) - len(data.lstrip())
        content_end = data_start + len(data.rstrip())
        self._nodes.append(
            TableTextNode(
                node_id=f"table-text-{len(self._nodes) + 1:04d}",
                text=stripped,
                start=content_start,
                end=content_end,
            )
        )

    def _absolute_position(self) -> int:
        line, offset = self.getpos()
        return self._line_offsets[line - 1] + offset


def _parse_html(source: str, *, collect_nodes: bool) -> _ParsedHTML:
    if not isinstance(source, str) or not source.strip():
        raise TableTranslationError("HTML 必须是非空字符串")
    parser = _TableHTMLParser(source, collect_nodes=collect_nodes)
    try:
        parser.feed(source)
        parser.close()
        return parser.result
    except TableTranslationError:
        raise
    except Exception as exc:
        raise TableTranslationError(f"HTML 解析失败：{exc}") from exc


def _parse_response(
    response: str,
    expected_ids: tuple[str, ...],
) -> dict[str, str]:
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TableTranslationError("模型响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise TableTranslationError("模型响应必须是 JSON 对象")
    values = payload.get("translations")
    if not isinstance(values, list):
        raise TableTranslationError("模型响应 translations 必须是数组")
    if len(values) != len(expected_ids):
        raise TableTranslationError("模型结果数量不一致")

    translations: dict[str, str] = {}
    for value in values:
        if not isinstance(value, dict):
            raise TableTranslationError("模型翻译项必须是对象")
        node_id = value.get("id")
        text = value.get("text")
        if not isinstance(node_id, str) or not node_id:
            raise TableTranslationError("模型翻译项缺少有效 ID")
        if node_id in translations:
            raise TableTranslationError("模型结果存在重复 ID")
        if not isinstance(text, str) or not text.strip():
            raise TableTranslationError("模型译文不能为空")
        translations[node_id] = text.strip()

    if set(translations) != set(expected_ids):
        raise TableTranslationError("模型结果 ID 集合不一致")
    return translations


def prepare_table_translation(table_html: str) -> PreparedTableTranslation:
    parsed = _parse_html(table_html, collect_nodes=True)
    return PreparedTableTranslation(
        original_html=table_html,
        nodes=parsed.nodes,
        structure=parsed.structure,
    )
```

- [ ] **Step 5: Run the pure module tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_table_translation.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit the pure table component**

```bash
git add src/pdf_trans/table_translation.py tests/test_table_translation.py
git commit -m "feat: parse and reconstruct translated tables"
```

---

### Task 2: Translation Orchestrator Dispatch, Retry, and Fallback

**Files:**
- Modify: `src/pdf_trans/translation.py:15-354`
- Modify: `tests/test_translation.py`

**Interfaces:**
- Consumes:
  - `prepare_table_translation(table_body) -> PreparedTableTranslation`;
  - existing `TextTranslator.translate(text: str) -> str`;
  - existing `max_retries`, `concurrency`, and checkpoint writer.
- Produces:
  - `TableTranslationOutcome`;
  - `_translate_table_one(...) -> TableTranslationOutcome`;
  - table objects with `translation_status` and optional `translated_table_body`;
  - unchanged `TranslationStats` shape and text-only counts.

- [ ] **Step 1: Write failing successful-table and numeric-only tests**

Append to `tests/test_translation.py`:

```python
class JsonTableTranslator:
    def __init__(self, translations):
        self.translations = translations
        self.received = []

    def translate(self, text):
        self.received.append(text)
        payload = json.loads(text)
        return json.dumps(
            {
                "translations": [
                    {
                        "id": item["id"],
                        "text": self.translations[item["text"]],
                    }
                    for item in reversed(payload["items"])
                ]
            },
            ensure_ascii=False,
        )


def test_translate_file_batches_table_nodes_by_id_and_preserves_source_html(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = (
        '<table><tr><th rowspan="2">Component</th>'
        '<th colspan="2">Mass Fraction</th></tr>'
        "<tr><td>Ethanol</td><td>100</td></tr></table>"
    )
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = JsonTableTranslator(
        {
            "Component": "组分",
            "Mass Fraction": "质量分数",
            "Ethanol": "乙醇",
        }
    )

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=0,
        concurrency=1,
    )

    assert len(translator.received) == 1
    request = json.loads(translator.received[0])
    assert [item["text"] for item in request["items"]] == [
        "Component",
        "Mass Fraction",
        "Ethanol",
    ]
    result = read_items(output)[0]
    assert result["table_body"] == table_body
    assert result["translated_table_body"] == (
        '<table><tr><th rowspan="2">组分</th>'
        '<th colspan="2">质量分数</th></tr>'
        "<tr><td>乙醇</td><td>100</td></tr></table>"
    )
    assert result["translation_status"] == "success"
    assert stats == TranslationStats(0, 1, 0, 0, 0, 0)


def test_numeric_only_table_makes_no_model_call_and_finishes_successfully(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>123</td><td>45.6</td></tr></table>"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        FakeTranslator([]),
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["translation_status"] == "success"
    assert result["translated_table_body"] == table_body
    assert stats.model_call_count == 0
```

- [ ] **Step 2: Run the two integration tests and verify RED**

Run:

```bash
.venv/bin/pytest \
  tests/test_translation.py::test_translate_file_batches_table_nodes_by_id_and_preserves_source_html \
  tests/test_translation.py::test_numeric_only_table_makes_no_model_call_and_finishes_successfully \
  -v
```

Expected: both fail because `type="table"` objects are still copied unchanged and no model call occurs.

- [ ] **Step 3: Write failing fallback, continuation, and resume tests**

Append to `tests/test_translation.py`:

```python
class SelectiveTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text):
        self.received.append(text)
        if text == "Paragraph":
            return "正文"
        raise RuntimeError("table service unavailable")


def test_table_translation_exception_falls_back_and_document_continues(
    tmp_path,
    caplog,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "Paragraph"},
                {"type": "table", "table_body": table_body},
            ]
        ),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="pdf_trans.translation")

    stats = translate_content_list_file(
        source,
        output,
        SelectiveTranslator(),
        max_retries=0,
        concurrency=1,
    )

    result = read_items(output)
    assert result[0]["translated_text"] == "正文"
    assert result[0]["translation_status"] == "success"
    assert result[1]["table_body"] == table_body
    assert "translated_table_body" not in result[1]
    assert result[1]["translation_status"] == "failed"
    assert result[1]["translation_error"] == "table service unavailable"
    assert stats == TranslationStats(1, 2, 0, 1, 0, 0)
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "第 1 张表翻译完成：failed" in messages
    assert table_body not in messages


@pytest.mark.parametrize(
    "table_body",
    [
        "<table><tr><td>Alpha</tr></table>",
        "<table><tr><td>Alpha</td></tr>",
    ],
)
def test_invalid_table_html_falls_back_without_model_call(tmp_path, table_body):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = FakeTranslator([])

    translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert translator.received == []
    assert result["table_body"] == table_body
    assert result["translation_status"] == "failed"
    assert "HTML" in result["translation_error"]


def test_resume_skips_successful_table_by_original_table_body(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    translated_table_body = "<table><tr><td>阿尔法</td></tr></table>"
    normalized = [{"type": "table", "table_body": table_body}]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(
        json.dumps(
            [
                {
                    **normalized[0],
                    "translated_table_body": translated_table_body,
                    "translation_status": "success",
                }
            ]
        ),
        encoding="utf-8",
    )
    translator = FakeTranslator([])

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    assert translator.received == []
    assert read_items(output)[0]["translated_table_body"] == translated_table_body
    assert stats == TranslationStats(0, 0, 0, 0, 0, 0)
```

Run:

```bash
.venv/bin/pytest \
  tests/test_translation.py::test_table_translation_exception_falls_back_and_document_continues \
  tests/test_translation.py::test_invalid_table_html_falls_back_without_model_call \
  tests/test_translation.py::test_resume_skips_successful_table_by_original_table_body \
  -v
```

Expected: failures show that tables have no status, fallback, resume identity, or logging path.

- [ ] **Step 4: Add the table outcome type and state helpers**

In `src/pdf_trans/translation.py`, add:

```python
from pdf_trans.table_translation import (
    TableTranslationError,
    prepare_table_translation,
)
```

After `TranslationOutcome`, add:

```python
@dataclass(frozen=True)
class TableTranslationOutcome:
    index: int
    table_number: int
    status: Literal["success", "failed"]
    translated_table_body: str | None
    error: str | None
    model_call_count: int
    elapsed_seconds: float
```

Extend `_same_identity` without changing its existing `type`/`text` checks:

```python
def _same_identity(
    normalized: dict[str, Any],
    existing: dict[str, Any],
) -> bool:
    for field in ("type", "text"):
        if (field in normalized) != (field in existing):
            return False
        if normalized.get(field) != existing.get(field):
            return False
    if normalized.get("type") == "table":
        if ("table_body" in normalized) != ("table_body" in existing):
            return False
        if normalized.get("table_body") != existing.get("table_body"):
            return False
    return True
```

Extend `_prepare_new_items`:

```python
def _prepare_new_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(items)
    for item in prepared:
        if item.get("type") == "text":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
        elif item.get("type") == "table":
            item.pop("translated_table_body", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
    return prepared
```

Add this helper before `_prepare_resumed_items`:

```python
def _restore_table_state(
    item: dict[str, Any],
    old: dict[str, Any],
    index: int,
) -> None:
    status = old.get("translation_status")
    if status is None:
        item.pop("translated_table_body", None)
        item.pop("translation_error", None)
        item["translation_status"] = "pending"
        return
    if status == "success":
        translated = old.get("translated_table_body")
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationContentError(
                f"断点文件第 {index} 个 success 表格缺少有效 "
                "translated_table_body"
            )
        item["translated_table_body"] = translated
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        return
    if status in {"pending", "failed"}:
        item.pop("translated_table_body", None)
        item["translation_status"] = status
        if status == "failed":
            error = old.get("translation_error")
            if not isinstance(error, str) or not error.strip():
                raise TranslationContentError(
                    f"断点文件第 {index} 个 failed 表格状态字段无效"
                )
            item["translation_error"] = error
        else:
            item.pop("translation_error", None)
        return
    raise TranslationContentError(
        f"断点文件第 {index} 个 table 对象的 translation_status 无效"
    )
```

At the top of the loop in `_prepare_resumed_items`, after creating `item`, add
the table branch before the existing non-text branch:

```python
        if current.get("type") == "table":
            _restore_table_state(item, old, index)
            prepared.append(item)
            continue
```

Leave the existing text status branch byte-for-byte unchanged.

- [ ] **Step 5: Implement table translation, work collection, and application**

Add after `_translate_one`:

```python
def _failed_table_outcome(
    *,
    index: int,
    table_number: int,
    error: Exception,
    model_call_count: int,
    started: float,
) -> TableTranslationOutcome:
    elapsed = time.perf_counter() - started
    message = str(error) or error.__class__.__name__
    LOGGER.error(
        "第 %d 张表翻译完成：failed，耗时 %.2f 秒，错误：%s",
        table_number,
        elapsed,
        message,
    )
    return TableTranslationOutcome(
        index=index,
        table_number=table_number,
        status="failed",
        translated_table_body=None,
        error=message,
        model_call_count=model_call_count,
        elapsed_seconds=elapsed,
    )


def _translate_table_one(
    index: int,
    table_number: int,
    table_body: Any,
    translator: TextTranslator,
    max_retries: int,
) -> TableTranslationOutcome:
    started = time.perf_counter()
    try:
        prepared = prepare_table_translation(table_body)
    except Exception as exc:
        return _failed_table_outcome(
            index=index,
            table_number=table_number,
            error=exc,
            model_call_count=0,
            started=started,
        )

    if not prepared.nodes:
        elapsed = time.perf_counter() - started
        LOGGER.info(
            "第 %d 张表无需翻译：success，耗时 %.2f 秒",
            table_number,
            elapsed,
        )
        return TableTranslationOutcome(
            index=index,
            table_number=table_number,
            status="success",
            translated_table_body=prepared.original_html,
            error=None,
            model_call_count=0,
            elapsed_seconds=elapsed,
        )

    request = prepared.build_request()
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        LOGGER.info(
            "第 %d 张表开始批量翻译 %d 个节点：第 %d/%d 次调用",
            table_number,
            len(prepared.nodes),
            attempt,
            total_attempts,
        )
        try:
            response = translator.translate(request)
            if not isinstance(response, str) or not response.strip():
                raise TableTranslationError("模型返回空表格翻译结果")
            translated = prepared.apply_response(response)
        except Exception as exc:
            if attempt < total_attempts:
                LOGGER.warning(
                    "第 %d 张表第 %d 次调用失败：%s，将重试",
                    table_number,
                    attempt,
                    exc,
                )
                continue
            return _failed_table_outcome(
                index=index,
                table_number=table_number,
                error=exc,
                model_call_count=attempt,
                started=started,
            )

        elapsed = time.perf_counter() - started
        LOGGER.info(
            "第 %d 张表翻译完成：success，耗时 %.2f 秒，%d 个节点",
            table_number,
            elapsed,
            len(prepared.nodes),
        )
        return TableTranslationOutcome(
            index=index,
            table_number=table_number,
            status="success",
            translated_table_body=translated,
            error=None,
            model_call_count=attempt,
            elapsed_seconds=elapsed,
        )

    raise AssertionError(f"第 {table_number} 张表未产生翻译结果")
```

Add after `_apply_outcome`:

```python
def _apply_table_outcome(
    item: dict[str, Any],
    outcome: TableTranslationOutcome,
) -> None:
    if outcome.status == "success":
        item["translated_table_body"] = outcome.translated_table_body
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        return
    item.pop("translated_table_body", None)
    item["translation_status"] = "failed"
    item["translation_error"] = outcome.error
```

Add after `_collect_translation_work`:

```python
def _collect_table_translation_work(
    items: list[dict[str, Any]],
) -> list[tuple[int, int, Any]]:
    work: list[tuple[int, int, Any]] = []
    table_number = 0
    for index, item in enumerate(items):
        if item.get("type") != "table":
            continue
        table_number += 1
        if item.get("translation_status") == "success":
            continue
        work.append((index, table_number, item.get("table_body")))
    return work
```

- [ ] **Step 6: Dispatch tables through the existing executor and checkpoints**

Replace the work/future section in `translate_content_list_file` with:

```python
    work = _collect_translation_work(items)
    table_work = _collect_table_translation_work(items)
    LOGGER.info(
        "准备并发翻译：待处理 %d 段、%d 张表，并发数 %d",
        len(work),
        len(table_work),
        concurrency,
    )
    model_calls = 0
    executor = ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="pdf-trans",
    )
    futures: list[Future[TranslationOutcome | TableTranslationOutcome]] = [
        executor.submit(
            _translate_one,
            index,
            section_number,
            text,
            translator,
            max_retries,
        )
        for index, section_number, text in work
    ]
    futures.extend(
        executor.submit(
            _translate_table_one,
            index,
            table_number,
            table_body,
            translator,
            max_retries,
        )
        for index, table_number, table_body in table_work
    )
    try:
        for future in as_completed(futures):
            outcome = future.result()
            if isinstance(outcome, TableTranslationOutcome):
                _apply_table_outcome(items[outcome.index], outcome)
            else:
                _apply_outcome(items[outcome.index], outcome)
            model_calls += outcome.model_call_count
            writer(output, items)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
```

Before building the existing text-only `counts`, add:

```python
    if any(
        item.get("type") == "table"
        and item.get("translation_status") == "pending"
        for item in items
    ):
        raise TranslationContentError("正式翻译结束后仍存在 pending 表格")
```

Do not change the existing `Counter` filter or `TranslationStats` constructor;
its success, failed, pending, and skipped counts remain text-only. Only
`model_call_count` now includes table calls.

- [ ] **Step 7: Run integration tests and the existing text regression suite**

Run:

```bash
.venv/bin/pytest tests/test_translation.py -v
```

Expected: all tests pass, including the pre-existing concurrency, retry,
checkpoint, interruption, logging, and text behavior tests.

- [ ] **Step 8: Add strict response-failure fallback coverage**

Append to `tests/test_translation.py`:

```python
@pytest.mark.parametrize(
    "response_value",
    [
        json.dumps({"translations": []}),
        json.dumps(
            {
                "translations": [
                    {"id": "table-text-9999", "text": "错误 ID"}
                ]
            }
        ),
        "not json",
    ],
)
def test_invalid_table_translation_response_falls_back(
    tmp_path,
    response_value,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    translate_content_list_file(
        source,
        output,
        FakeTranslator([response_value]),
        max_retries=0,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["table_body"] == table_body
    assert "translated_table_body" not in result
    assert result["translation_status"] == "failed"
    assert result["translation_error"]
```

Run:

```bash
.venv/bin/pytest \
  tests/test_translation.py::test_invalid_table_translation_response_falls_back \
  -v
```

Expected: all parameter cases pass.

- [ ] **Step 9: Commit orchestrator integration**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: dispatch and checkpoint table translations"
```

---

### Task 3: Render Successful Table Translations

**Files:**
- Modify: `tests/test_renderer.py`
- Modify: `src/pdf_trans/renderer.py:53-59`

**Interfaces:**
- Consumes: `translation_status`, original `table_body`, and optional `translated_table_body`.
- Produces: translated HTML only for a successful non-blank table translation; original HTML for failed, blank, pending, or legacy table objects.

- [ ] **Step 1: Write failing renderer selection and fallback tests**

Append to `tests/test_renderer.py`:

```python
def test_render_items_uses_successful_translated_table_body():
    item = {
        "type": "table",
        "table_caption": ["表 1"],
        "table_body": "<table><tr><td>Alpha</td></tr></table>",
        "translated_table_body": "<table><tr><td>阿尔法</td></tr></table>",
        "translation_status": "success",
        "table_footnote": ["注"],
    }

    assert render_items([item]) == (
        "表 1\n\n"
        "<table><tr><td>阿尔法</td></tr></table>\n\n"
        "注\n"
    )


@pytest.mark.parametrize("status", ["failed", "pending", None])
def test_render_items_falls_back_to_original_table_body(status):
    item = {
        "type": "table",
        "table_body": "<table><tr><td>Alpha</td></tr></table>",
        "translated_table_body": "<table><tr><td>阿尔法</td></tr></table>",
    }
    if status is not None:
        item["translation_status"] = status

    assert render_items([item]) == (
        "<table><tr><td>Alpha</td></tr></table>\n"
    )


def test_render_items_falls_back_when_successful_table_translation_is_blank():
    item = {
        "type": "table",
        "table_body": "<table><tr><td>Alpha</td></tr></table>",
        "translated_table_body": "   ",
        "translation_status": "success",
    }

    assert render_items([item]) == (
        "<table><tr><td>Alpha</td></tr></table>\n"
    )
```

- [ ] **Step 2: Run renderer tests and verify RED**

Run:

```bash
.venv/bin/pytest \
  tests/test_renderer.py::test_render_items_uses_successful_translated_table_body \
  tests/test_renderer.py::test_render_items_falls_back_to_original_table_body \
  tests/test_renderer.py::test_render_items_falls_back_when_successful_table_translation_is_blank \
  -v
```

Expected: the first test fails because the renderer still always uses
`table_body`; fallback tests already pass and guard the existing behavior.

- [ ] **Step 3: Select translated HTML only for validated success**

Replace the table branch in `src/pdf_trans/renderer.py` with:

```python
    if item_type == "table":
        parts = _string_list(item.get("table_caption"))
        table_body = item.get("table_body")
        translated_table_body = item.get("translated_table_body")
        if (
            item.get("translation_status") == "success"
            and _is_non_blank_string(translated_table_body)
        ):
            table_body = translated_table_body
        if _is_non_blank_string(table_body):
            parts.append(table_body)
        parts.extend(_string_list(item.get("table_footnote")))
        return parts
```

- [ ] **Step 4: Run renderer and translation tests**

Run:

```bash
.venv/bin/pytest tests/test_renderer.py tests/test_translation.py tests/test_table_translation.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit rendering**

```bash
git add src/pdf_trans/renderer.py tests/test_renderer.py
git commit -m "feat: render translated table html"
```

---

### Task 4: Full Verification and Scope Review

**Files:**
- Verify all modified files; no new implementation files in this task.

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: evidence that the new table path works and existing text, CLI, web, workflow, and rendering behavior did not regress.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/pytest \
  tests/test_table_translation.py \
  tests/test_translation.py \
  tests/test_renderer.py \
  -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the complete suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: all tests pass with no warnings introduced by this change.

- [ ] **Step 3: Run static repository checks**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints nothing. `git status --short` shows only
intentional table-translation files if implementation commits have not yet
been made, or is clean after the planned commits.

- [ ] **Step 4: Review the final diff against scope**

Run:

```bash
git diff HEAD~3 -- \
  src/pdf_trans/table_translation.py \
  src/pdf_trans/translation.py \
  src/pdf_trans/renderer.py \
  tests/test_table_translation.py \
  tests/test_translation.py \
  tests/test_renderer.py
```

Confirm:

- `TextTranslator.translate()` and the HTTP client were reused, not duplicated.
- No HTML tag or attribute is included in a table model request.
- One table request contains all and only eligible text nodes.
- Numeric-only and whitespace-only nodes are absent from requests.
- Response mapping is ID-based and validates exact count and ID set.
- Original `table_body` is never overwritten.
- Table failures cannot raise out of the worker into whole-document processing.
- Existing paragraph functions `_translate_one()` and `_apply_outcome()` retain
  their previous behavior.
- No formula, footnote, external-caption, or cross-table logic was added.
