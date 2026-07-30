# Translation Formula Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent translation models from seeing or modifying MinerU formulas while preserving the existing retry, failure, checkpoint, HTML, and original-content rendering behavior.

**Architecture:** Add a focused formula-protection module that scans `$...$` and `$$...$$`, replaces them with request-scoped hashed placeholders, validates model output, and restores the original strings. Create one protection context per paragraph or table request, connect it to the two existing model paths, and raise validation errors inside the existing retry loops so unsafe output can never become a successful translation.

**Tech Stack:** Python 3.11+, standard-library `dataclasses`, `hashlib`, `re`, `secrets`, existing `json`/`html.parser`, pytest 8

## Global Constraints

- Do not modify or repair MinerU formula recognition.
- Do not modify the frontend formula renderer.
- Protect complete inline `$...$` and block `$$...$$` formulas before every translation-model call.
- The model must never receive recognized original LaTeX.
- Restore formulas byte-for-byte from locally saved source strings, never from model output.
- Reject missing, duplicate, added, tampered, moved, or reordered placeholders.
- Reuse existing retries; exhausted validation failures must be marked `failed`, never `success`.
- Failed text and tables must continue rendering original MinerU `text` and `table_body`.
- Preserve table tags, nesting, attributes, `rowspan`, and `colspan`.
- Keep table translation item reordering by `table-text-*` ID valid while rejecting formula reordering inside a node.
- Do not add runtime dependencies.

## File Structure

- Create `src/pdf_trans/formula_protection.py`: formula scanning, request-scoped placeholder creation, output validation, hash checking, and exact restoration.
- Create `tests/test_formula_protection.py`: isolated scanner, preservation, and adversarial response tests.
- Modify `src/pdf_trans/table_translation.py`: protect every extracted table text node and restore formulas before existing HTML escaping/reconstruction.
- Modify `tests/test_table_translation.py`: verify formulas never enter table requests and that HTML/formulas survive reconstruction.
- Modify `src/pdf_trans/translation.py`: protect ordinary paragraphs before the retry loop and restore inside each attempt.
- Modify `tests/test_translation.py`: verify end-to-end paragraph/table retry and status behavior and update the old raw-formula request expectation.

---

### Task 1: Formula protection component

**Files:**
- Create: `tests/test_formula_protection.py`
- Create: `src/pdf_trans/formula_protection.py`

**Interfaces:**
- Produces: `FormulaProtectionError(ValueError)`
- Produces: `ProtectedText(model_text: str, formula_ids: tuple[str, ...])`
- Produces: `FormulaProtectionContext.protect(text: str) -> ProtectedText`
- Produces: `FormulaProtectionContext.restore(translated_text: str, protected: ProtectedText) -> str`
- Guarantee: the context owns all originals and SHA-256 values; callers never reconstruct formulas

- [ ] **Step 1: Write failing extraction and exact-restoration tests**

Create `tests/test_formula_protection.py`:

```python
import re

import pytest

from pdf_trans.formula_protection import (
    FormulaProtectionContext,
    FormulaProtectionError,
)


PLACEHOLDER_RE = re.compile(
    r"⟪PDFTRANS_FORMULA:[0-9a-f]{16}:\d{4}:[0-9a-f]{64}⟫"
)


def test_protects_single_and_multiple_inline_formulas_and_restores_exactly():
    source = (
        r"At $x^{2}+\mathrm{kg}/\mathrm{m}^{3}$ and "
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$."
    )
    context = FormulaProtectionContext()

    protected = context.protect(source)

    assert "$x" not in protected.model_text
    assert r"\mathrm" not in protected.model_text
    assert len(PLACEHOLDER_RE.findall(protected.model_text)) == 2
    translated = protected.model_text.replace("At ", "在 ").replace(" and ", " 和 ")
    assert context.restore(translated, protected) == (
        r"在 $x^{2}+\mathrm{kg}/\mathrm{m}^{3}$ 和 "
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$."
    )


def test_protects_multiline_block_formula_and_preserves_every_character():
    formula = "$$\nE = mc^{2} + \\frac{a}{b}\n$$"
    source = f"Before\n{formula}\nAfter"
    context = FormulaProtectionContext()

    protected = context.protect(source)
    restored = context.restore(
        protected.model_text.replace("Before", "之前").replace("After", "之后"),
        protected,
    )

    assert formula not in protected.model_text
    assert restored == f"之前\n{formula}\n之后"


def test_escaped_dollar_and_unclosed_delimiter_remain_plain_text():
    source = r"Cost \$5 and an unclosed $value"
    context = FormulaProtectionContext()

    protected = context.protect(source)

    assert protected.model_text == source
    assert protected.formula_ids == ()
    assert context.restore(source, protected) == source
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest -q tests/test_formula_protection.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'pdf_trans.formula_protection'`.

- [ ] **Step 3: Write failing adversarial validation tests**

Append to `tests/test_formula_protection.py`:

```python
def _protected_pair():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect("Left $x$ middle $y$ right")
    placeholders = PLACEHOLDER_RE.findall(protected.model_text)
    return context, protected, placeholders


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda text, values: text.replace(values[0], ""), "缺失"),
        (lambda text, values: text + values[0], "重复"),
        (
            lambda text, values: text + (
                "⟪PDFTRANS_FORMULA:0123456789abcdef:9999:"
                + "0" * 64
                + "⟫"
            ),
            "新增",
        ),
        (
            lambda text, values: text.replace(
                values[0], values[0].replace("0123456789abcdef", "fedcba9876543210")
            ),
            "篡改",
        ),
        (
            lambda text, values: text.replace(values[0], "__FIRST__", 1)
            .replace(values[1], values[0], 1)
            .replace("__FIRST__", values[1], 1),
            "顺序",
        ),
    ],
)
def test_rejects_invalid_placeholder_responses(mutate, message):
    context, protected, placeholders = _protected_pair()

    with pytest.raises(FormulaProtectionError, match=message):
        context.restore(mutate(protected.model_text, placeholders), protected)


def test_rejects_model_generated_formula_before_restoration():
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect("Value $x$")

    with pytest.raises(FormulaProtectionError, match="新增公式"):
        context.restore(protected.model_text + " and $y$", protected)


def test_rejects_reserved_sentinel_in_source():
    context = FormulaProtectionContext()

    with pytest.raises(FormulaProtectionError, match="保留哨兵"):
        context.protect("source PDFTRANS_FORMULA marker")
```

- [ ] **Step 4: Implement the minimal scanner, protection context, and validator**

Create `src/pdf_trans/formula_protection.py` with:

```python
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass


_SENTINEL_MARKER = "PDFTRANS_FORMULA"
_PLACEHOLDER_RE = re.compile(
    r"⟪PDFTRANS_FORMULA:([0-9a-f]{16}):(\d{4}):([0-9a-f]{64})⟫"
)


class FormulaProtectionError(ValueError):
    """A model response cannot be safely restored to the source formulas."""


@dataclass(frozen=True)
class ProtectedText:
    model_text: str
    formula_ids: tuple[str, ...]


@dataclass(frozen=True)
class _FormulaRecord:
    formula_id: str
    original: str
    digest: str
    placeholder: str


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _find_closing(text: str, start: int, delimiter: str) -> int | None:
    position = start
    while position < len(text):
        if (
            text.startswith(delimiter, position)
            and not _is_escaped(text, position)
            and (delimiter == "$$" or not text.startswith("$$", position))
        ):
            return position
        position += 1
    return None


def _formula_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = []
    position = 0
    while position < len(text):
        if text.startswith("$$", position) and not _is_escaped(text, position):
            close = _find_closing(text, position + 2, "$$")
            if close is not None:
                spans.append((position, close + 2))
                position = close + 2
                continue
            position += 2
            continue
        if text[position] == "$" and not _is_escaped(text, position):
            close = _find_closing(text, position + 1, "$")
            if close is not None:
                spans.append((position, close + 1))
                position = close + 1
                continue
        position += 1
    return tuple(spans)


def _extract_formulas(text: str) -> tuple[str, ...]:
    return tuple(text[start:end] for start, end in _formula_spans(text))


class FormulaProtectionContext:
    def __init__(self, *, nonce: str | None = None) -> None:
        self._nonce = nonce or secrets.token_hex(8)
        if re.fullmatch(r"[0-9a-f]{16}", self._nonce) is None:
            raise ValueError("formula protection nonce must be 16 lowercase hex digits")
        self._records: dict[str, _FormulaRecord] = {}

    def protect(self, text: str) -> ProtectedText:
        if _SENTINEL_MARKER in text:
            raise FormulaProtectionError("原文包含公式保护保留哨兵")
        parts = []
        formula_ids = []
        cursor = 0
        for start, end in _formula_spans(text):
            parts.append(text[cursor:start])
            original = text[start:end]
            formula_id = f"{len(self._records) + 1:04d}"
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
            placeholder = (
                f"⟪{_SENTINEL_MARKER}:{self._nonce}:{formula_id}:{digest}⟫"
            )
            self._records[formula_id] = _FormulaRecord(
                formula_id, original, digest, placeholder
            )
            formula_ids.append(formula_id)
            parts.append(placeholder)
            cursor = end
        parts.append(text[cursor:])
        return ProtectedText("".join(parts), tuple(formula_ids))

    def restore(self, translated_text: str, protected: ProtectedText) -> str:
        matches = list(_PLACEHOLDER_RE.finditer(translated_text))
        expected = [self._records[value] for value in protected.formula_ids]
        expected_ids = list(protected.formula_ids)
        actual_ids = [match.group(2) for match in matches]

        if translated_text.count(_SENTINEL_MARKER) != len(matches):
            raise FormulaProtectionError("公式占位符被篡改")
        duplicates = sorted(
            value for value in set(actual_ids) if actual_ids.count(value) > 1
        )
        if duplicates:
            raise FormulaProtectionError("公式占位符存在重复 ID")
        unknown = sorted(set(actual_ids) - set(expected_ids))
        if unknown:
            raise FormulaProtectionError("公式占位符存在新增 ID")
        missing = sorted(set(expected_ids) - set(actual_ids))
        if missing:
            raise FormulaProtectionError("公式占位符存在缺失 ID")
        if len(matches) != len(expected):
            raise FormulaProtectionError("公式占位符数量不一致")
        for match in matches:
            record = self._records[match.group(2)]
            if (
                match.group(1) != self._nonce
                or match.group(3) != record.digest
                or match.group(0) != record.placeholder
            ):
                raise FormulaProtectionError("公式占位符被篡改")
        if actual_ids != expected_ids:
            raise FormulaProtectionError("公式占位符顺序变化")
        if _extract_formulas(translated_text):
            raise FormulaProtectionError("模型返回中存在新增公式")

        records_by_placeholder = {
            record.placeholder: record for record in expected
        }

        def replace(match: re.Match[str]) -> str:
            record = records_by_placeholder[match.group(0)]
            digest = hashlib.sha256(record.original.encode("utf-8")).hexdigest()
            if digest != record.digest:
                raise FormulaProtectionError("原始公式哈希校验失败")
            return record.original

        restored = _PLACEHOLDER_RE.sub(replace, translated_text)
        restored_formulas = _extract_formulas(restored)
        originals = tuple(record.original for record in expected)
        if restored_formulas != originals:
            raise FormulaProtectionError("恢复后的公式内容不一致")
        if _SENTINEL_MARKER in restored:
            raise FormulaProtectionError("恢复后仍存在公式保护哨兵")
        return restored
```

- [ ] **Step 5: Run component tests and verify GREEN**

Run:

```bash
pytest -q tests/test_formula_protection.py
```

Expected: all formula-protection tests pass.

- [ ] **Step 6: Commit the component**

```bash
git add src/pdf_trans/formula_protection.py tests/test_formula_protection.py
git commit -m "feat: add translation formula protection"
```

---

### Task 2: Protect and restore table text nodes

**Files:**
- Modify: `tests/test_table_translation.py`
- Modify: `src/pdf_trans/table_translation.py`

**Interfaces:**
- Consumes: `FormulaProtectionContext`, `ProtectedText`
- Changes: `TableTextNode` stores `protected_text: ProtectedText`
- Changes: `PreparedTableTranslation` stores one shared `formula_context`
- Guarantee: `build_request()` serializes only `protected_text.model_text`
- Guarantee: `apply_response()` restores formulas per node before HTML escaping

- [ ] **Step 1: Write failing table formula and structure tests**

Append to `tests/test_table_translation.py`:

```python
def test_table_request_hides_formulas_and_restores_them_in_original_cells():
    formula_one = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    formula_two = r"$x^{2}+\frac{a}{b}$"
    source = (
        '<table class="source"><tr>'
        f'<td rowspan="2">Temperature {formula_one}</td>'
        f'<td colspan="3">First {formula_two} then $y_{{1}}$</td>'
        "</tr></table>"
    )
    prepared = prepare_table_translation(source)
    payload = json.loads(prepared.build_request())

    serialized = json.dumps(payload, ensure_ascii=False)
    assert formula_one not in serialized
    assert formula_two not in serialized
    assert r"\circ" not in serialized
    assert len({value for value in re.findall(
        r"⟪PDFTRANS_FORMULA:[^⟫]+⟫", serialized
    )}) == 3

    translated_items = []
    for item in payload["items"]:
        translated_items.append(
            (
                item["id"],
                item["text"]
                .replace("Temperature", "温度")
                .replace("First", "先")
                .replace("then", "后"),
            )
        )
    translated = prepared.apply_response(response(*reversed(translated_items)))

    assert formula_one in translated
    assert formula_two in translated
    assert "$y_{1}$" in translated
    assert 'class="source"' in translated
    assert 'rowspan="2"' in translated
    assert 'colspan="3"' in translated


def test_table_rejects_formula_moved_between_cells():
    prepared = prepare_table_translation(
        "<table><tr><td>Alpha $x$</td><td>Beta $y$</td></tr></table>"
    )
    payload = json.loads(prepared.build_request())
    first = payload["items"][0]["text"]
    second = payload["items"][1]["text"]
    first_token = re.search(r"⟪PDFTRANS_FORMULA:[^⟫]+⟫", first).group(0)
    second_token = re.search(r"⟪PDFTRANS_FORMULA:[^⟫]+⟫", second).group(0)

    with pytest.raises(TableTranslationError, match="缺失|新增"):
        prepared.apply_response(
            response(
                (payload["items"][0]["id"], first.replace(first_token, second_token)),
                (payload["items"][1]["id"], second.replace(second_token, first_token)),
            )
        )
```

Add `import re` to the test imports.

- [ ] **Step 2: Run the two tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_table_translation.py::test_table_request_hides_formulas_and_restores_them_in_original_cells \
  tests/test_table_translation.py::test_table_rejects_formula_moved_between_cells
```

Expected: the first test fails because raw LaTeX is still present in the JSON request.

- [ ] **Step 3: Connect protection to table preparation and reconstruction**

In `src/pdf_trans/table_translation.py`:

```python
from pdf_trans.formula_protection import (
    FormulaProtectionContext,
    FormulaProtectionError,
    ProtectedText,
)
```

Add `protected_text: ProtectedText` to `TableTextNode`. Add
`formula_context: FormulaProtectionContext` to `PreparedTableTranslation`.
Change request serialization from `node.text` to
`node.protected_text.model_text`.

Use these exact dataclass fields:

```python
@dataclass(frozen=True)
class TableTextNode:
    node_id: str
    text: str
    start: int
    end: int
    protected_text: ProtectedText


@dataclass(frozen=True)
class PreparedTableTranslation:
    original_html: str
    nodes: tuple[TableTextNode, ...]
    structure: tuple[tuple[object, ...], ...]
    formula_context: FormulaProtectionContext
```

In `PreparedTableTranslation.apply_response()`, restore before escaping:

```python
        translated = self.original_html
        for node in reversed(self.nodes):
            try:
                restored_text = self.formula_context.restore(
                    translations[node.node_id],
                    node.protected_text,
                )
            except FormulaProtectionError as exc:
                raise TableTranslationError(
                    f"表格节点 {node.node_id} 公式校验失败：{exc}"
                ) from exc
            translated = (
                translated[: node.start]
                + escape(restored_text, quote=False)
                + translated[node.end :]
            )
```

Keep HTML parsing independent of formula protection by first creating parser
nodes with a temporary formula-free `ProtectedText(stripped, ())`, then in
`prepare_table_translation()` create one context and replace every node:

```python
        self._nodes.append(
            TableTextNode(
                node_id=f"table-text-{len(self._nodes) + 1:04d}",
                text=stripped,
                start=content_start,
                end=content_end,
                protected_text=ProtectedText(stripped, ()),
            )
        )


def prepare_table_translation(table_html: str) -> PreparedTableTranslation:
    parsed = _parse_html(table_html, collect_nodes=True)
    formula_context = FormulaProtectionContext()
    nodes = tuple(
        TableTextNode(
            node_id=node.node_id,
            text=node.text,
            start=node.start,
            end=node.end,
            protected_text=formula_context.protect(node.text),
        )
        for node in parsed.nodes
    )
    return PreparedTableTranslation(
        original_html=table_html,
        nodes=nodes,
        structure=parsed.structure,
        formula_context=formula_context,
    )
```

- [ ] **Step 4: Run table protocol tests and verify GREEN**

Run:

```bash
pytest -q tests/test_table_translation.py
```

Expected: all tests pass, including the existing exact HTML structure and
`table-text-*` response reordering tests.

- [ ] **Step 5: Commit table integration**

```bash
git add src/pdf_trans/table_translation.py tests/test_table_translation.py
git commit -m "feat: protect formulas in table translation"
```

---

### Task 3: Protect paragraph translation and exercise retry/status behavior

**Files:**
- Modify: `tests/test_translation.py`
- Modify: `src/pdf_trans/translation.py`

**Interfaces:**
- Consumes: `FormulaProtectionContext.protect()` and `.restore()`
- Guarantee: one context is created before the retry loop and reused by all attempts
- Guarantee: restore errors remain inside the existing `try/except`, so they retry
- Guarantee: exhausted validation errors produce the existing failed outcome

- [ ] **Step 1: Write failing paragraph success and retry tests**

Add to `tests/test_translation.py`:

```python
FORMULA_TOKEN_RE = re.compile(r"⟪PDFTRANS_FORMULA:[^⟫]+⟫")


class PlaceholderProbeTranslator:
    def __init__(self, *, corrupt_attempts=0):
        self.corrupt_attempts = corrupt_attempts
        self.received = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        assert response_format is None
        if len(self.received) <= self.corrupt_attempts:
            return FORMULA_TOKEN_RE.sub("", text, count=1)
        return text.replace("Temperature", "温度").replace("and density", "和密度")


def test_text_translation_hides_multiple_formulas_and_restores_exactly(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    formula_one = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    formula_two = "$$\n\\rho = \\frac{m}{V}\n$$"
    source_text = f"Temperature {formula_one} and density {formula_two}"
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )
    translator = PlaceholderProbeTranslator()

    translate_content_list_file(source, output, translator, concurrency=1)

    assert formula_one not in translator.received[0]
    assert formula_two not in translator.received[0]
    result = read_items(output)[0]
    assert result["translated_text"] == f"温度 {formula_one} 和密度 {formula_two}"
    assert result["translation_status"] == "success"


def test_placeholder_validation_failure_retries_then_succeeds(tmp_path, caplog):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "text", "text": "Temperature $x$"}]),
        encoding="utf-8",
    )
    translator = PlaceholderProbeTranslator(corrupt_attempts=1)
    caplog.set_level(logging.INFO, logger="pdf_trans.translation")

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )

    assert len(translator.received) == 2
    assert translator.received[0] == translator.received[1]
    assert stats.model_call_count == 2
    assert read_items(output)[0]["translation_status"] == "success"
    assert "公式占位符" in "\n".join(
        record.getMessage() for record in caplog.records
    )
```

Add `import re` to the test imports.

- [ ] **Step 2: Write failing exhausted-retry fallback test**

Append:

```python
def test_placeholder_validation_exhaustion_never_writes_success(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source_text = "Temperature $x^{2}$"
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        PlaceholderProbeTranslator(corrupt_attempts=2),
        max_retries=1,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["translated_text"] is None
    assert result["translation_status"] == "failed"
    assert "公式占位符" in result["translation_error"]
    assert stats == TranslationStats(1, 2, 0, 0, 1, 0)
```

- [ ] **Step 3: Update the old raw-formula model-input expectation**

In `test_translate_file_processes_every_text_object_and_preserves_non_text`,
change the second text object from:

```python
        {"type": "text", "text": "Beta $x^2$", "custom": {"a": 1}},
```

to:

```python
        {"type": "text", "text": "Beta value", "custom": {"a": 1}},
```

Change its fake result from `"乙 $x^2$"` to `"乙值"` and change the received
request assertion to:

```python
    assert translator.received == ["Alpha [38]", "Beta value", "Gamma C-1"]
```

The focused tests above now own all formula behavior; this broad orchestration
test continues checking object preservation without asserting obsolete raw
formula exposure.

- [ ] **Step 4: Run focused translation tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_translation.py::test_text_translation_hides_multiple_formulas_and_restores_exactly \
  tests/test_translation.py::test_placeholder_validation_failure_retries_then_succeeds \
  tests/test_translation.py::test_placeholder_validation_exhaustion_never_writes_success
```

Expected: the first test fails because raw formulas still reach the translator.

- [ ] **Step 5: Integrate protection into `_translate_one`**

Import the component in `src/pdf_trans/translation.py`:

```python
from pdf_trans.formula_protection import (
    FormulaProtectionContext,
    FormulaProtectionError,
)
```

Before the retry loop in `_translate_one`, prepare once:

```python
    formula_context = FormulaProtectionContext()
    try:
        protected = formula_context.protect(text)
    except FormulaProtectionError as exc:
        elapsed = time.perf_counter() - started
        error = f"公式保护准备失败：{exc}"
        LOGGER.error(
            "第 %d 段翻译完成：failed，耗时 %.2f 秒，错误：%s",
            section_number,
            elapsed,
            error,
        )
        return TranslationOutcome(
            index=index,
            section_number=section_number,
            status="failed",
            translated_text=None,
            error=error,
            model_call_count=0,
            elapsed_seconds=elapsed,
        )
```

Inside the existing attempt `try`, replace the model call and validate before
leaving the block:

```python
            translated = translator.translate(protected.model_text)
            if not isinstance(translated, str) or not translated.strip():
                raise ValueError("模型返回空译文")
            translated = formula_context.restore(translated, protected)
```

Do not add a separate retry mechanism. Existing exception logging and
`TranslationOutcome(status="failed")` handling must remain the only state path.

- [ ] **Step 6: Add end-to-end table validation failure coverage**

Add this fake and test to `tests/test_translation.py`:

```python
class CorruptingTableFormulaTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        assert response_format is not None
        payload = json.loads(text)
        return json.dumps(
            {
                "translations": [
                    {
                        "id": item["id"],
                        "text": FORMULA_TOKEN_RE.sub("", item["text"], count=1),
                    }
                    for item in payload["items"]
                ]
            }
        )


def test_table_formula_validation_exhaustion_keeps_original_table(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = (
        "<table><tr><td>"
        r"Temperature ${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
        "</td></tr></table>"
    )
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = CorruptingTableFormulaTranslator()

    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )

    assert len(translator.received) == 2
    result = read_items(output)[0]
    assert result["table_body"] == table_body
    assert "translated_table_body" not in result
    assert result["translation_status"] == "failed"
    assert "表格节点 table-text-0001 公式校验失败" in result["translation_error"]
```

- [ ] **Step 7: Run translation tests and verify GREEN**

Run:

```bash
pytest -q tests/test_translation.py
```

Expected: all translation orchestration, retry, checkpoint, concurrency, text,
and table tests pass.

- [ ] **Step 8: Commit orchestration integration**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: enforce formula-safe translation outcomes"
```

---

### Task 4: Full regression verification

**Files:**
- Verify only; no planned production changes

**Interfaces:**
- Verifies all global constraints and both model-entry paths

- [ ] **Step 1: Run formatting and whitespace checks**

Run:

```bash
git diff --check HEAD~3
```

Expected: exit code 0 with no output.

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run the package build**

Run:

```bash
python3 -m build
```

Expected: exit code 0 and both sdist and wheel are created.

- [ ] **Step 4: Audit the final model-call surface**

Run:

```bash
rg -n "translator\\.translate\\(" src/pdf_trans
```

Expected: only the ordinary text call using `protected.model_text` and the
table call using `prepared.build_request()` remain.

- [ ] **Step 5: Audit the required behavior against the diff**

Confirm from tests and code:

- text and table formulas are absent from model payloads;
- inline, multiple, block, backslash, brace, and command content restore exactly;
- placeholder missing, duplicate, added, tampered, and order changes fail;
- formula validation errors occur inside existing retries;
- exhausted errors cannot write success;
- original MinerU text/table remains available for renderer fallback;
- table tags and attributes are unchanged;
- no frontend or MinerU-recognition code changed.

- [ ] **Step 6: Commit verification-only fixes if needed**

If verification exposes a defect, return to the relevant task's RED-GREEN cycle.
Do not create an empty verification commit.
