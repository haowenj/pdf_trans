# Cell-Level Table Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace atomic whole-table translation with formula-safe, cell-level adaptive batching so a failed cell falls back to its original MinerU HTML without discarding successful cells in the same table.

**Architecture:** Parse `table_body` with `HTMLParser` into stable cell records while retaining exact source offsets and original markup. Protect each cell's formulas and inline HTML with short local markers, split only oversized cells at safe boundaries, translate work items in budgeted JSON batches, validate every returned work item independently, and rebuild the table by replacing only successfully translated cell interiors. `translation.py` coordinates adaptive retries and writes partial-success metadata; ordinary `text` translation remains on its existing path.

**Tech Stack:** Python 3.11+, standard-library `html.parser`, `html`, `hashlib`, `json`, `re`, existing translator protocol, pytest.

## Global Constraints

- Do not modify ordinary `text` translation behavior, MinerU formula recognition/correction, or the frontend renderer.
- Discover `table`, `tr`, `td`, `th`, `caption`, `colspan`, and `rowspan` through an HTML parser; never split table HTML with regular expressions.
- Never send table tags, cell tags, attributes, or raw LaTeX to the translation model.
- Formula IDs are local to each logical cell and use the exact stable marker format `⟦M0⟧`, `⟦M1⟧`, and so on.
- Inline markup uses cell-local markers `⟦H0⟧`, `⟦H1⟧`, and so on; count, set, uniqueness, and order must match before restoration.
- First-round batch limits are exactly 12 work items, 2,000 estimated input tokens, and 24 formula markers.
- Model responses use `{"cells":[{"cell_id":"...","translated_text":"..."}]}` and are locally validated even when strict JSON Schema is enabled.
- Retry only failed work items, halve the item limit on intermediate rounds, and force one work item per request on the final round.
- If any segment of an oversized cell ultimately fails, preserve that cell's complete original inner HTML.
- A safely reconstructed table is `translation_status=success`; partial cell fallback is represented by `table_translation_partial`, success/fallback counts, and ordered fallback details.
- Only invalid input HTML or an unsafe final reconstruction may produce table-level `translation_status=failed`.

---

## File Map

- Create `src/pdf_trans/table_cell_protection.py`: cell-local formula and markup marker protection, validation, exact restoration, token estimation, and safe segmentation.
- Rewrite `src/pdf_trans/table_translation.py`: parser-backed cell extraction, conservative skip rules, work-item and batch models, JSON protocol parsing, and final source-offset reconstruction.
- Modify `src/pdf_trans/translation.py`: adaptive batch/retry orchestration, cell-level fallback aggregation, logs, metadata application, and checkpoint restoration.
- Create `tests/test_table_cell_protection.py`: focused marker, hash, formula, markup, token, and segmentation tests.
- Rewrite `tests/test_table_translation.py`: parser, skip, batch, partial response, and reconstruction unit tests against the new API.
- Modify `tests/test_translation.py`: model-call sequencing, retries, cell fallback, metadata, checkpoint, and table-level failure integration tests.

### Task 1: Cell-local formula and markup protection

**Files:**
- Create: `src/pdf_trans/table_cell_protection.py`
- Create: `tests/test_table_cell_protection.py`

**Interfaces:**
- Consumes: no new project interfaces; formula scanning is implemented locally so `FormulaProtectionContext` used by ordinary text remains unchanged.
- Produces:
  - `CellProtectionError(ValueError)`
  - `CellMarker(marker_id: str, placeholder: str, original: str, sha256: str)`
  - `ProtectedCellSegment(model_text: str, formula_markers: tuple[CellMarker, ...], html_markers: tuple[CellMarker, ...])`
  - `protect_cell_text(text: str, html_markers: tuple[CellMarker, ...] = ()) -> ProtectedCellSegment`
  - `restore_cell_segment(translated_text: str, protected: ProtectedCellSegment) -> str`
  - `estimate_model_tokens(text: str) -> int`
  - `split_protected_segment(protected: ProtectedCellSegment, max_tokens: int, max_formulas: int = 24) -> tuple[ProtectedCellSegment, ...]`

- [ ] **Step 1: Write failing tests for local formula extraction and exact restoration**

Create `tests/test_table_cell_protection.py` with these tests:

```python
import pytest

from pdf_trans.table_cell_protection import (
    CellMarker,
    CellProtectionError,
    estimate_model_tokens,
    protect_cell_text,
    restore_cell_segment,
    split_protected_segment,
)


def test_protects_inline_and_block_formulas_with_cell_local_ids():
    source = (
        r"Density ${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$, "
        r"then $x^{2}+\frac{a}{b}$ and $$\max_{i \neq j} a_{ij}$$"
    )

    protected = protect_cell_text(source)

    assert protected.model_text == (
        "Density ⟦M0⟧, then ⟦M1⟧ and ⟦M2⟧"
    )
    assert [marker.original for marker in protected.formula_markers] == [
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$",
        r"$x^{2}+\frac{a}{b}$",
        r"$$\max_{i \neq j} a_{ij}$$",
    ]
    assert restore_cell_segment(
        "密度 ⟦M0⟧，然后 ⟦M1⟧ 和 ⟦M2⟧",
        protected,
    ) == (
        r"密度 ${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$，"
        r"然后 $x^{2}+\frac{a}{b}$ 和 $$\max_{i \neq j} a_{ij}$$"
    )


def test_formula_ids_restart_for_each_cell():
    first = protect_cell_text(r"Alpha $x$")
    second = protect_cell_text(r"Beta $y$")

    assert first.model_text == "Alpha ⟦M0⟧"
    assert second.model_text == "Beta ⟦M0⟧"
    assert first.formula_markers[0].original == "$x$"
    assert second.formula_markers[0].original == "$y$"


@pytest.mark.parametrize(
    ("translated", "message"),
    [
        ("甲", "缺失"),
        ("甲 ⟦M0⟧ ⟦M0⟧", "重复"),
        ("甲 ⟦M0⟧ ⟦M1⟧", "新增"),
        ("甲 ⟦M-0⟧", "篡改"),
    ],
)
def test_rejects_missing_duplicate_added_or_mutated_formula_marker(
    translated,
    message,
):
    protected = protect_cell_text(r"Alpha $x$")

    with pytest.raises(CellProtectionError, match=message):
        restore_cell_segment(translated, protected)


def test_rejects_formula_marker_order_change():
    protected = protect_cell_text(r"Alpha $x$ Beta $y$")

    with pytest.raises(CellProtectionError, match="顺序"):
        restore_cell_segment("甲 ⟦M1⟧ 乙 ⟦M0⟧", protected)


def test_preserves_backslashes_braces_and_latex_commands_byte_for_byte():
    formula = (
        r"$$\left\{\frac{\complement A}{\max_{i\neqq j}x_{ij}}\right\}$$"
    )
    protected = protect_cell_text(f"Value {formula}")

    restored = restore_cell_segment("值 ⟦M0⟧", protected)

    assert restored.encode("utf-8").endswith(formula.encode("utf-8"))
```

- [ ] **Step 2: Run the protection tests to verify the module is missing**

Run:

```bash
pytest -q tests/test_table_cell_protection.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'pdf_trans.table_cell_protection'`.

- [ ] **Step 3: Implement local scanning, marker validation, and hash-checked restoration**

Create `src/pdf_trans/table_cell_protection.py` with these concrete rules:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape


_MARKER_RE = re.compile(r"⟦([MH])(\d+)⟧")


class CellProtectionError(ValueError):
    """A translated cell cannot be restored without changing protected data."""


@dataclass(frozen=True)
class CellMarker:
    marker_id: str
    placeholder: str
    original: str
    sha256: str


@dataclass(frozen=True)
class ProtectedCellSegment:
    model_text: str
    formula_markers: tuple[CellMarker, ...]
    html_markers: tuple[CellMarker, ...] = ()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _formula_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if text[index] != "$" or (index > 0 and text[index - 1] == "\\"):
            index += 1
            continue
        delimiter = "$$" if text.startswith("$$", index) else "$"
        cursor = index + len(delimiter)
        while cursor < len(text):
            if text.startswith(delimiter, cursor) and (
                cursor == 0 or text[cursor - 1] != "\\"
            ):
                spans.append((index, cursor + len(delimiter)))
                index = cursor + len(delimiter)
                break
            cursor += 1
        else:
            index += len(delimiter)
    return tuple(spans)


def protect_cell_text(
    text: str,
    html_markers: tuple[CellMarker, ...] = (),
) -> ProtectedCellSegment:
    if not isinstance(text, str):
        raise CellProtectionError("单元格文本必须是字符串")
    if "⟦M" in text:
        raise CellProtectionError("原文包含保留的公式标记")
    _validate_marker_family(text, "H", html_markers)
    pieces: list[str] = []
    markers: list[CellMarker] = []
    cursor = 0
    for number, (start, end) in enumerate(_formula_spans(text)):
        formula = text[start:end]
        placeholder = f"⟦M{number}⟧"
        pieces.extend((text[cursor:start], placeholder))
        markers.append(
            CellMarker(
                marker_id=f"M{number}",
                placeholder=placeholder,
                original=formula,
                sha256=_digest(formula),
            )
        )
        cursor = end
    pieces.append(text[cursor:])
    return ProtectedCellSegment(
        model_text="".join(pieces),
        formula_markers=tuple(markers),
        html_markers=html_markers,
    )


def _validate_marker_family(
    text: str,
    family: str,
    expected: tuple[CellMarker, ...],
) -> None:
    found = [
        f"{kind}{number}"
        for kind, number in _MARKER_RE.findall(text)
        if kind == family
    ]
    expected_ids = [marker.marker_id for marker in expected]
    if len(found) < len(expected_ids):
        raise CellProtectionError(f"{family} 占位符缺失")
    if len(found) > len(set(found)):
        raise CellProtectionError(f"{family} 占位符重复")
    additions = set(found) - set(expected_ids)
    if additions:
        raise CellProtectionError(f"{family} 占位符新增")
    if set(found) != set(expected_ids):
        raise CellProtectionError(f"{family} 占位符 ID 集合不一致")
    if found != expected_ids:
        raise CellProtectionError(f"{family} 占位符顺序变化")


def restore_cell_segment(
    translated_text: str,
    protected: ProtectedCellSegment,
) -> str:
    if not isinstance(translated_text, str) or not translated_text.strip():
        raise CellProtectionError("单元格译文不能为空")
    if "$" in translated_text:
        raise CellProtectionError("模型译文新增了未保护公式")
    without_valid_markers = _MARKER_RE.sub("", translated_text)
    if "⟦M" in without_valid_markers or "⟦H" in without_valid_markers:
        raise CellProtectionError("占位符被篡改")
    _validate_marker_family(
        translated_text,
        "M",
        protected.formula_markers,
    )
    _validate_marker_family(
        translated_text,
        "H",
        protected.html_markers,
    )
    restored = "".join(
        part
        if _MARKER_RE.fullmatch(part)
        else escape(part, quote=False)
        for part in re.split(r"(⟦[MH]\d+⟧)", translated_text)
    )
    for marker in (*protected.formula_markers, *protected.html_markers):
        if _digest(marker.original) != marker.sha256:
            raise CellProtectionError(f"{marker.marker_id} 本地哈希校验失败")
        restored = restored.replace(marker.placeholder, marker.original, 1)
    if _MARKER_RE.search(restored):
        raise CellProtectionError("占位符被篡改或未完全恢复")
    for marker in protected.formula_markers:
        if marker.original not in restored:
            raise CellProtectionError(f"{marker.marker_id} 公式恢复失败")
    return restored
```

Escaping happens before formula and HTML restoration. Therefore model-introduced
`<` and `&` become safe text while `&`, braces, and backslashes inside the
locally stored formula remain byte-for-byte unchanged. The implementation must
report human-readable Chinese reasons because those messages are persisted in
`table_translation_fallbacks`.

- [ ] **Step 4: Run the formula protection tests**

Run:

```bash
pytest -q tests/test_table_cell_protection.py
```

Expected: all tests from Step 1 pass.

- [ ] **Step 5: Add failing tests for markup markers, token estimates, and safe segmentation**

Append:

```python
def test_restores_html_markers_exactly_and_rejects_reordering():
    html_markers = (
        CellMarker("H0", "⟦H0⟧", '<strong class="x">', "ignored"),
        CellMarker("H1", "⟦H1⟧", "</strong>", "ignored"),
    )
    html_markers = tuple(
        CellMarker(
            marker.marker_id,
            marker.placeholder,
            marker.original,
            __import__("hashlib").sha256(
                marker.original.encode("utf-8")
            ).hexdigest(),
        )
        for marker in html_markers
    )
    protected = protect_cell_text(
        r"⟦H0⟧Value $x$⟦H1⟧",
        html_markers=html_markers,
    )

    assert restore_cell_segment(
        "⟦H0⟧值 ⟦M0⟧⟦H1⟧",
        protected,
    ) == '<strong class="x">值 $x$</strong>'
    with pytest.raises(CellProtectionError, match="H 占位符顺序"):
        restore_cell_segment("⟦H1⟧值 ⟦M0⟧⟦H0⟧", protected)


def test_estimates_ascii_and_cjk_tokens_conservatively():
    assert estimate_model_tokens("abcd") == 1
    assert estimate_model_tokens("中文") == 2
    assert estimate_model_tokens("ab中文") == 3


def test_splits_at_break_marker_without_cutting_formula_or_marker():
    br = '<br data-kind="source">'
    digest = __import__("hashlib").sha256(br.encode("utf-8")).hexdigest()
    protected = protect_cell_text(
        "First sentence ⟦H0⟧Second $x^{2}$ sentence.",
        html_markers=(CellMarker("H0", "⟦H0⟧", br, digest),),
    )

    segments = split_protected_segment(protected, max_tokens=6)

    assert [segment.model_text for segment in segments] == [
        "First sentence ⟦H0⟧",
        "Second ⟦M0⟧ sentence.",
    ]
    assert segments[0].html_markers[0].placeholder == "⟦H0⟧"
    assert segments[1].formula_markers[0].placeholder == "⟦M0⟧"
    assert all("⟦M" not in segment.model_text or "⟧" in segment.model_text
               for segment in segments)


def test_does_not_hard_cut_an_unbreakable_protected_token():
    protected = protect_cell_text(r"Prefix $abcdefghijklmnop$ suffix")

    segments = split_protected_segment(protected, max_tokens=1)

    assert any(segment.model_text == "⟦M0⟧" for segment in segments)
    assert restore_cell_segment("⟦M0⟧", segments[1]) == "$abcdefghijklmnop$"


def test_splits_before_exceeding_formula_budget():
    protected = protect_cell_text(
        r"One $a$ two $b$ three $c$ four $d$ five $e$"
    )

    segments = split_protected_segment(
        protected,
        max_tokens=1_000,
        max_formulas=2,
    )

    assert [len(segment.formula_markers) for segment in segments] == [2, 2, 1]
```

- [ ] **Step 6: Implement token estimation and marker-aware segmentation**

Add to `src/pdf_trans/table_cell_protection.py`:

```python
_ATOMIC_MARKER_RE = re.compile(r"⟦[MH]\d+⟧")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？;；])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


def estimate_model_tokens(text: str) -> int:
    ascii_count = sum(ord(character) < 128 for character in text)
    non_ascii_count = len(text) - ascii_count
    return max(1, (ascii_count + 3) // 4 + non_ascii_count)


def _marker_subset(
    text: str,
    markers: tuple[CellMarker, ...],
) -> tuple[CellMarker, ...]:
    return tuple(marker for marker in markers if marker.placeholder in text)


def _safe_chunks(text: str) -> list[str]:
    marker_spans = list(_ATOMIC_MARKER_RE.finditer(text))
    blocked = {
        offset
        for match in marker_spans
        for offset in range(match.start() + 1, match.end())
    }
    preferred: set[int] = set()
    for match in re.finditer(r"⟦[MH]\d+⟧", text):
        preferred.add(match.end())
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        preferred.add(match.end())
    for match in _WHITESPACE_RE.finditer(text):
        preferred.add(match.end())
    boundaries = sorted(
        boundary
        for boundary in preferred
        if 0 < boundary < len(text) and boundary not in blocked
    )
    chunks: list[str] = []
    cursor = 0
    for boundary in boundaries:
        if boundary > cursor:
            chunks.append(text[cursor:boundary])
            cursor = boundary
    if cursor < len(text):
        chunks.append(text[cursor:])
    return chunks or [text]


def split_protected_segment(
    protected: ProtectedCellSegment,
    max_tokens: int,
    max_formulas: int = 24,
) -> tuple[ProtectedCellSegment, ...]:
    if max_tokens <= 0 or max_formulas <= 0:
        raise ValueError("segment 限制必须大于 0")
    if (
        estimate_model_tokens(protected.model_text) <= max_tokens
        and len(protected.formula_markers) <= max_formulas
    ):
        return (protected,)
    chunks = _safe_chunks(protected.model_text)
    grouped: list[str] = []
    current = ""
    for chunk in chunks:
        candidate = current + chunk
        candidate_formula_count = len(
            re.findall(r"⟦M\d+⟧", candidate)
        )
        if current and (
            estimate_model_tokens(candidate) > max_tokens
            or candidate_formula_count > max_formulas
        ):
            grouped.append(current)
            current = chunk
        else:
            current = candidate
    if current:
        grouped.append(current)
    return tuple(
        ProtectedCellSegment(
            model_text=group,
            formula_markers=_marker_subset(
                group,
                protected.formula_markers,
            ),
            html_markers=_marker_subset(group, protected.html_markers),
        )
        for group in grouped
        if group
    )
```

Before accepting the step, adjust `_validate_marker_family` so an empty expected family rejects any marker of that family and accepts none, and compute every `CellMarker.sha256` through `_digest`.

- [ ] **Step 7: Run the focused suite and commit**

Run:

```bash
pytest -q tests/test_table_cell_protection.py
```

Expected: all tests pass.

Commit:

```bash
git add src/pdf_trans/table_cell_protection.py tests/test_table_cell_protection.py
git commit -m "feat: add cell-local table content protection"
```

### Task 2: Parser-backed cell extraction and safe table reconstruction

**Files:**
- Rewrite: `src/pdf_trans/table_translation.py`
- Rewrite: `tests/test_table_translation.py`

**Interfaces:**
- Consumes:
  - `protect_cell_text(...)`
  - `restore_cell_segment(...)`
  - `split_protected_segment(...)`
  - `estimate_model_tokens(...)`
- Produces:
  - `TableTranslationError(ValueError)`
  - `CellWorkItem(work_id, cell_id, segment_index, model_text, estimated_tokens, formula_count)`
  - `CellFallback(cell_id: str, error: str)`
  - `PreparedTableTranslation.original_html`
  - `PreparedTableTranslation.cells`
  - `PreparedTableTranslation.work_items`
  - `PreparedTableTranslation.rebuild(translations: dict[str, str], errors: dict[str, str]) -> TableRebuildResult`
  - `prepare_table_translation(table_html: str) -> PreparedTableTranslation`

- [ ] **Step 1: Replace node-level tests with failing cell-level parser and protection tests**

Replace `tests/test_table_translation.py` with imports and the first parser tests:

```python
import json

import pytest

from pdf_trans.table_translation import (
    TableTranslationError,
    prepare_table_translation,
)


def test_extracts_stable_cells_and_never_sends_outer_html_or_attributes():
    source = (
        '<table class="source"><caption>Operating Data</caption>'
        '<tr><th rowspan="2">Component</th><th>123</th></tr>'
        '<tr><td colspan="3"><em>Mass</em> Fraction</td>'
        '<td data-code="A1">D1298 / IP 160</td></tr></table>'
    )

    prepared = prepare_table_translation(source)

    assert [cell.cell_id for cell in prepared.cells] == [
        "cell-0001",
        "cell-0002",
        "cell-0003",
        "cell-0004",
        "cell-0005",
    ]
    assert [work.work_id for work in prepared.work_items] == [
        "cell-0001",
        "cell-0002",
        "cell-0004",
    ]
    serialized = json.dumps(
        [
            {"cell_id": work.work_id, "text": work.model_text}
            for work in prepared.work_items
        ],
        ensure_ascii=False,
    )
    assert "<table" not in serialized
    assert "<em" not in serialized
    assert "rowspan" not in serialized
    assert "colspan" not in serialized
    assert "data-code" not in serialized
    assert "⟦H0⟧Mass⟦H1⟧ Fraction" in serialized


def test_formula_ids_restart_in_each_cell_and_latex_is_hidden():
    first = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    second = r"$x^{2}+\frac{a}{b}$ and $y_{1}$"
    source = (
        "<table><tr>"
        f"<td>Density {first}</td>"
        f"<td>Values {second}</td>"
        "</tr></table>"
    )

    prepared = prepare_table_translation(source)

    assert [work.model_text for work in prepared.work_items] == [
        "Density ⟦M0⟧",
        "Values ⟦M0⟧ and ⟦M1⟧",
    ]
    request_text = " ".join(
        work.model_text for work in prepared.work_items
    )
    assert r"\circ" not in request_text
    assert r"\frac" not in request_text


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
```

- [ ] **Step 2: Run parser tests and confirm the old API fails**

Run:

```bash
pytest -q tests/test_table_translation.py
```

Expected: failures mention missing `cells` or `work_items`, proving the tests are exercising the new contract.

- [ ] **Step 3: Implement the parsed cell model and conservative skip rules**

Rewrite `src/pdf_trans/table_translation.py` around these exact public records:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from pdf_trans.table_cell_protection import (
    CellMarker,
    CellProtectionError,
    ProtectedCellSegment,
    estimate_model_tokens,
    protect_cell_text,
    restore_cell_segment,
    split_protected_segment,
)


MAX_BATCH_ITEMS = 12
MAX_BATCH_TOKENS = 2_000
MAX_BATCH_FORMULAS = 24
REQUEST_TOKEN_OVERHEAD = 80
ITEM_TOKEN_OVERHEAD = 12
CELL_SEGMENT_TOKEN_BUDGET = (
    MAX_BATCH_TOKENS
    - REQUEST_TOKEN_OVERHEAD
    - ITEM_TOKEN_OVERHEAD
    - 16
)
_CELL_TAGS = frozenset({"td", "th", "caption"})
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})
_ENGLISH_RE = re.compile(r"[A-Za-z]")
_PURE_NUMBER_RE = re.compile(
    r"^[\s+\-–—.,:;/()%‰°\d]+$"
)
_PURE_STANDARD_CODE_RE = re.compile(
    r"^(?:(?:ASTM\s+)?D\d+[A-Z]?|IP\s*\d+|UOP\s*\d+|"
    r"[A-Z]{1,4}-?\d+[A-Z0-9-]*)(?:\s*[/,;]\s*"
    r"(?:(?:ASTM\s+)?D\d+[A-Z]?|IP\s*\d+|UOP\s*\d+|"
    r"[A-Z]{1,4}-?\d+[A-Z0-9-]*))*$",
    re.IGNORECASE,
)


class TableTranslationError(ValueError):
    """One table cannot be parsed or safely reconstructed."""


@dataclass(frozen=True)
class CellWorkItem:
    work_id: str
    cell_id: str
    segment_index: int
    model_text: str
    estimated_tokens: int
    formula_count: int


@dataclass(frozen=True)
class CellFallback:
    cell_id: str
    error: str


@dataclass(frozen=True)
class PreparedCell:
    cell_id: str
    tag: str
    content_start: int
    content_end: int
    original_inner_html: str
    segments: tuple[ProtectedCellSegment, ...]
    work_ids: tuple[str, ...]
    skipped: bool


@dataclass(frozen=True)
class TableRebuildResult:
    translated_html: str
    success_cell_count: int
    fallback_cell_count: int
    fallbacks: tuple[CellFallback, ...]


@dataclass(frozen=True)
class PreparedTableTranslation:
    original_html: str
    cells: tuple[PreparedCell, ...]
    structure: tuple[tuple[object, ...], ...]

    @property
    def work_items(self) -> tuple[CellWorkItem, ...]:
        items: list[CellWorkItem] = []
        for cell in self.cells:
            for index, (work_id, segment) in enumerate(
                zip(cell.work_ids, cell.segments)
            ):
                items.append(
                    CellWorkItem(
                        work_id=work_id,
                        cell_id=cell.cell_id,
                        segment_index=index,
                        model_text=segment.model_text,
                        estimated_tokens=(
                            ITEM_TOKEN_OVERHEAD
                            + estimate_model_tokens(work_id)
                            + estimate_model_tokens(segment.model_text)
                        ),
                        formula_count=len(segment.formula_markers),
                    )
                )
        return tuple(items)
```

Implement `_TableHTMLParser` with `convert_charrefs=False`, source line offsets, a validated tag stack, and a current-cell record. Every start/end/start-end tag inside a cell is captured as an exact source slice and replaced in model text by a locally numbered `CellMarker("Hn", "⟦Hn⟧", raw_tag, sha256)`. The outer cell start and end tags establish `content_start` and `content_end` but are not included in the cell's model text. Entity and character references inside a cell are protected as `H` markers so their spelling remains exact.

Use these exact private records and parser methods:

```python
@dataclass(frozen=True)
class _RawCell:
    cell_id: str
    tag: str
    content_start: int
    content_end: int
    original_inner_html: str
    model_source: str
    html_markers: tuple[CellMarker, ...]


@dataclass
class _OpenCell:
    cell_id: str
    tag: str
    content_start: int
    pieces: list[str]
    html_markers: list[CellMarker]


@dataclass(frozen=True)
class _ParsedHTML:
    cells: tuple[_RawCell, ...]
    structure: tuple[tuple[object, ...], ...]


class _TableHTMLParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_offsets = [0]
        self.line_offsets.extend(
            match.end() for match in re.finditer(r"\n", source)
        )
        self.stack: list[str] = []
        self.cells: list[_RawCell] = []
        self.structure: list[tuple[object, ...]] = []
        self.current: _OpenCell | None = None
        self.saw_table = False

    def _absolute(self) -> int:
        line, offset = self.getpos()
        return self.line_offsets[line - 1] + offset

    def _raw_until_gt(self) -> str:
        start = self._absolute()
        end = self.source.find(">", start)
        if end < 0:
            raise TableTranslationError("HTML 标签缺少结束符")
        return self.source[start:end + 1]

    def _protect_raw(self, raw: str) -> None:
        if self.current is None:
            return
        number = len(self.current.html_markers)
        placeholder = f"⟦H{number}⟧"
        self.current.pieces.append(placeholder)
        self.current.html_markers.append(
            CellMarker(
                marker_id=f"H{number}",
                placeholder=placeholder,
                original=raw,
                sha256=hashlib.sha256(
                    raw.encode("utf-8")
                ).hexdigest(),
            )
        )

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        raw = self.get_starttag_text()
        self.structure.append(("start", tag, tuple(attrs)))
        if tag == "table":
            self.saw_table = True
        if tag in _CELL_TAGS:
            if self.current is not None:
                raise TableTranslationError("HTML 中存在嵌套单元格")
            self.current = _OpenCell(
                cell_id=f"cell-{len(self.cells) + 1:04d}",
                tag=tag,
                content_start=self._absolute() + len(raw),
                pieces=[],
                html_markers=[],
            )
        elif self.current is not None:
            self._protect_raw(raw)
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        raw = self.get_starttag_text()
        self.structure.append(("self", tag, tuple(attrs)))
        if tag == "table":
            self.saw_table = True
        if tag in _CELL_TAGS:
            raise TableTranslationError("单元格标签不能自闭合")
        self._protect_raw(raw)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.stack or self.stack[-1] != tag:
            expected = self.stack[-1] if self.stack else "无"
            raise TableTranslationError(
                f"HTML 结束标签不匹配：期望 {expected}，实际 {tag}"
            )
        raw = self._raw_until_gt()
        if self.current is not None and tag == self.current.tag:
            content_end = self._absolute()
            self.cells.append(
                _RawCell(
                    cell_id=self.current.cell_id,
                    tag=tag,
                    content_start=self.current.content_start,
                    content_end=content_end,
                    original_inner_html=self.source[
                        self.current.content_start:content_end
                    ],
                    model_source="".join(self.current.pieces),
                    html_markers=tuple(self.current.html_markers),
                )
            )
            self.current = None
        elif self.current is not None:
            self._protect_raw(raw)
        self.stack.pop()
        self.structure.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if any(tag in {"script", "style", "template"} for tag in self.stack):
            self._protect_raw(data)
        else:
            self.current.pieces.append(data)

    def handle_entityref(self, name: str) -> None:
        self._protect_raw(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._protect_raw(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._protect_raw(f"<!--{data}-->")

    @property
    def result(self) -> _ParsedHTML:
        if self.current is not None or self.stack:
            raise TableTranslationError("HTML 标签未闭合")
        if not self.saw_table:
            raise TableTranslationError("HTML 中缺少 table 标签")
        return _ParsedHTML(tuple(self.cells), tuple(self.structure))


def _parse_html(source: str) -> _ParsedHTML:
    if not isinstance(source, str) or not source.strip():
        raise TableTranslationError("HTML 必须是非空字符串")
    parser = _TableHTMLParser(source)
    try:
        parser.feed(source)
        parser.close()
        return parser.result
    except TableTranslationError:
        raise
    except Exception as exc:
        raise TableTranslationError(f"HTML 解析失败：{exc}") from exc
```

Use this exact eligibility function after removing `M`/`H` markers:

```python
def _needs_translation(visible_text: str, formula_count: int) -> bool:
    plain = re.sub(r"⟦[MH]\d+⟧", "", visible_text).strip()
    if not plain:
        return False
    if _ENGLISH_RE.search(plain) is None:
        return False
    if _PURE_NUMBER_RE.fullmatch(plain):
        return False
    if _PURE_STANDARD_CODE_RE.fullmatch(plain):
        return False
    return True
```

Use `split_protected_segment(protected, MAX_BATCH_TOKENS)` only when the protected cell exceeds the single-item token budget. A single segment uses `work_id == cell_id`; multiple segments use `f"{cell_id}:segment-{index + 1:04d}"`.

Finish the module with these builders and lookup method:

```python
def _build_cell(raw: _RawCell) -> PreparedCell:
    try:
        protected = protect_cell_text(
            raw.model_source,
            html_markers=raw.html_markers,
        )
    except CellProtectionError as exc:
        raise TableTranslationError(
            f"单元格 {raw.cell_id} 保护失败：{exc}"
        ) from exc
    if not _needs_translation(
        protected.model_text,
        len(protected.formula_markers),
    ):
        return PreparedCell(
            cell_id=raw.cell_id,
            tag=raw.tag,
            content_start=raw.content_start,
            content_end=raw.content_end,
            original_inner_html=raw.original_inner_html,
            segments=(),
            work_ids=(),
            skipped=True,
        )
    segments = split_protected_segment(
        protected,
        CELL_SEGMENT_TOKEN_BUDGET,
        max_formulas=MAX_BATCH_FORMULAS,
    )
    if len(segments) == 1:
        work_ids = (raw.cell_id,)
    else:
        work_ids = tuple(
            f"{raw.cell_id}:segment-{index + 1:04d}"
            for index in range(len(segments))
        )
    return PreparedCell(
        cell_id=raw.cell_id,
        tag=raw.tag,
        content_start=raw.content_start,
        content_end=raw.content_end,
        original_inner_html=raw.original_inner_html,
        segments=segments,
        work_ids=work_ids,
        skipped=False,
    )


def _segment_for(
    cells: tuple[PreparedCell, ...],
    work_id: str,
) -> ProtectedCellSegment:
    for cell in cells:
        for current_id, segment in zip(cell.work_ids, cell.segments):
            if current_id == work_id:
                return segment
    raise KeyError(work_id)


def prepare_table_translation(
    table_html: str,
) -> PreparedTableTranslation:
    parsed = _parse_html(table_html)
    return PreparedTableTranslation(
        original_html=table_html,
        cells=tuple(_build_cell(raw) for raw in parsed.cells),
        structure=parsed.structure,
    )
```

Implement `PreparedTableTranslation.segment_for` as:

```python
def segment_for(self, work_id: str) -> ProtectedCellSegment:
    return _segment_for(self.cells, work_id)
```

- [ ] **Step 4: Add failing reconstruction and skip-rule tests**

Append to `tests/test_table_translation.py`:

```python
def test_rebuilds_only_successful_cells_and_preserves_exact_outer_structure():
    formula = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    source = (
        '<table class="source"><tr>'
        f'<td rowspan="2">Density {formula}</td>'
        '<td colspan="3"><em>Mass</em> Fraction</td>'
        '<td data-code="A1">Failure $x$</td>'
        "</tr></table>"
    )
    prepared = prepare_table_translation(source)

    result = prepared.rebuild(
        {
            "cell-0001": "密度 ⟦M0⟧",
            "cell-0002": "⟦H0⟧质量⟦H1⟧ 分数",
        },
        {"cell-0003": "公式占位符被篡改"},
    )

    assert result.translated_html == (
        '<table class="source"><tr>'
        f'<td rowspan="2">密度 {formula}</td>'
        '<td colspan="3"><em>质量</em> 分数</td>'
        '<td data-code="A1">Failure $x$</td>'
        "</tr></table>"
    )
    assert result.success_cell_count == 2
    assert result.fallback_cell_count == 1
    assert result.fallbacks[0].cell_id == "cell-0003"
    assert 'rowspan="2"' in result.translated_html
    assert 'colspan="3"' in result.translated_html
    assert 'data-code="A1"' in result.translated_html


def test_skips_empty_numeric_formula_and_standard_code_cells():
    prepared = prepare_table_translation(
        "<table><tr>"
        "<td> </td><td>775 to 840</td><td>$x^{2}$</td>"
        "<td>D1298 / IP 160</td><td>D1298 or IP 160</td>"
        "</tr></table>"
    )

    assert [work.work_id for work in prepared.work_items] == [
        "cell-0002",
        "cell-0005",
    ]


def test_any_failed_segment_falls_back_the_whole_cell():
    long_text = "First sentence. " * 700
    source = f"<table><tr><td>{long_text}</td></tr></table>"
    prepared = prepare_table_translation(source)
    assert len(prepared.work_items) > 1
    translations = {
        work.work_id: "第一句。"
        for work in prepared.work_items[:-1]
    }
    last = prepared.work_items[-1]

    result = prepared.rebuild(
        translations,
        {last.work_id: "模型返回空译文"},
    )

    assert result.translated_html == source
    assert result.success_cell_count == 0
    assert result.fallback_cell_count == 1
```

- [ ] **Step 5: Implement validated restoration and offset-based rebuild**

Add `PreparedTableTranslation.rebuild` using this algorithm:

```python
def rebuild(
    self,
    translations: dict[str, str],
    errors: dict[str, str],
) -> TableRebuildResult:
    replacements: list[tuple[int, int, str]] = []
    success_count = 0
    fallbacks: list[CellFallback] = []
    for cell in self.cells:
        if cell.skipped:
            continue
        missing = [
            work_id
            for work_id in cell.work_ids
            if work_id not in translations
        ]
        if missing:
            reason = next(
                (
                    errors[work_id]
                    for work_id in missing
                    if work_id in errors
                ),
                "模型结果缺少单元格译文",
            )
            fallbacks.append(CellFallback(cell.cell_id, reason))
            continue
        try:
            restored_parts = [
                restore_cell_segment(translations[work_id], segment)
                for work_id, segment in zip(cell.work_ids, cell.segments)
            ]
            restored_inner = "".join(restored_parts)
        except CellProtectionError as exc:
            fallbacks.append(CellFallback(cell.cell_id, str(exc)))
            continue
        replacements.append(
            (cell.content_start, cell.content_end, restored_inner)
        )
        success_count += 1

    translated = self.original_html
    for start, end, replacement in reversed(replacements):
        translated = translated[:start] + replacement + translated[end:]
    parsed = _parse_html(translated)
    if parsed.structure != self.structure:
        raise TableTranslationError("翻译后的 HTML 结构校验失败")
    return TableRebuildResult(
        translated_html=translated,
        success_cell_count=success_count,
        fallback_cell_count=len(fallbacks),
        fallbacks=tuple(fallbacks),
    )
```

`restore_cell_segment` already escapes translated text chunks before restoring
locally stored markers. Therefore `restored_inner` must be joined directly;
do not escape the returned string a second time. This guarantees model-created
markup is rendered as text while original HTML markers and formulas are restored
verbatim.

- [ ] **Step 6: Run the parser/rebuild tests and commit**

Run:

```bash
pytest -q tests/test_table_translation.py
```

Expected: all tests pass.

Commit:

```bash
git add src/pdf_trans/table_translation.py tests/test_table_translation.py
git commit -m "feat: parse and rebuild table translations by cell"
```

### Task 3: Budgeted JSON batches and per-item response validation

**Files:**
- Modify: `src/pdf_trans/table_translation.py`
- Modify: `tests/test_table_translation.py`

**Interfaces:**
- Consumes: `CellWorkItem` from Task 2.
- Produces:
  - `CellTranslationBatch(items: tuple[CellWorkItem, ...])`
  - `ParsedBatchResponse(translations: dict[str, str], errors: dict[str, str], warnings: tuple[str, ...])`
  - `plan_cell_batches(items, *, max_items, max_tokens, max_formulas) -> tuple[CellTranslationBatch, ...]`
  - `CellTranslationBatch.build_request() -> str`
  - `CellTranslationBatch.build_response_format() -> dict[str, object]`
  - `CellTranslationBatch.parse_response(response: str) -> ParsedBatchResponse`

- [ ] **Step 1: Add failing tests for all three budget limits**

Append:

```python
from pdf_trans.table_translation import (
    CellWorkItem,
    plan_cell_batches,
)


def _work(number, *, tokens=10, formulas=0):
    return CellWorkItem(
        work_id=f"cell-{number:04d}",
        cell_id=f"cell-{number:04d}",
        segment_index=0,
        model_text=f"Text {number}",
        estimated_tokens=tokens,
        formula_count=formulas,
    )


def test_batch_planner_enforces_item_token_and_formula_limits():
    by_items = plan_cell_batches(
        tuple(_work(number) for number in range(1, 14)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )
    by_tokens = plan_cell_batches(
        (_work(1, tokens=1_500), _work(2, tokens=600)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )
    by_formulas = plan_cell_batches(
        (_work(1, formulas=20), _work(2, formulas=5)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )

    assert [len(batch.items) for batch in by_items] == [12, 1]
    assert [len(batch.items) for batch in by_tokens] == [1, 1]
    assert [len(batch.items) for batch in by_formulas] == [1, 1]
```

- [ ] **Step 2: Add failing protocol and partial-response tests**

Append:

```python
def test_batch_request_and_schema_only_contain_current_work_items():
    batch = plan_cell_batches(
        (_work(1), _work(2)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]

    request = json.loads(batch.build_request())
    schema = batch.build_response_format()

    assert request["cells"] == [
        {"cell_id": "cell-0001", "text": "Text 1"},
        {"cell_id": "cell-0002", "text": "Text 2"},
    ]
    assert schema["json_schema"]["schema"]["properties"]["cells"][
        "items"
    ]["properties"]["cell_id"]["enum"] == ["cell-0001", "cell-0002"]


def test_partial_response_keeps_valid_item_and_isolates_missing_duplicate():
    batch = plan_cell_batches(
        (_work(1), _work(2), _work(3)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]
    response = json.dumps(
        {
            "cells": [
                {"cell_id": "cell-0001", "translated_text": "甲"},
                {"cell_id": "cell-0002", "translated_text": "乙"},
                {"cell_id": "cell-0002", "translated_text": "重复"},
                {"cell_id": "cell-9999", "translated_text": "未知"},
            ]
        },
        ensure_ascii=False,
    )

    parsed = batch.parse_response(response)

    assert parsed.translations == {"cell-0001": "甲"}
    assert parsed.errors == {
        "cell-0002": "模型结果存在重复 cell_id",
        "cell-0003": "模型结果缺少 cell_id",
    }
    assert parsed.warnings == ("模型结果包含未知 cell_id: cell-9999",)


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        "[]",
        '{"cells":{}}',
        '{"cells":[],"extra":true}',
    ],
)
def test_invalid_top_level_response_fails_the_batch(response):
    batch = plan_cell_batches(
        (_work(1), _work(2)),
        max_items=12,
        max_tokens=2_000,
        max_formulas=24,
    )[0]

    with pytest.raises(TableTranslationError):
        batch.parse_response(response)
```

- [ ] **Step 3: Implement greedy planning and strict request/schema generation**

Add:

```python
@dataclass(frozen=True)
class ParsedBatchResponse:
    translations: dict[str, str]
    errors: dict[str, str]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class CellTranslationBatch:
    items: tuple[CellWorkItem, ...]

    def build_request(self) -> str:
        return json.dumps(
            {
                "task": (
                    "Translate each cell text from English to Simplified "
                    "Chinese. Return JSON only. Preserve every ⟦M...⟧ and "
                    "⟦H...⟧ marker exactly and in the same order."
                ),
                "cells": [
                    {"cell_id": item.work_id, "text": item.model_text}
                    for item in self.items
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def build_response_format(self) -> dict[str, object]:
        ids = [item.work_id for item in self.items]
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "table_cell_translation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "cells": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "cell_id": {
                                        "type": "string",
                                        "enum": ids,
                                    },
                                    "translated_text": {
                                        "type": "string",
                                        "minLength": 1,
                                    },
                                },
                                "required": [
                                    "cell_id",
                                    "translated_text",
                                ],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["cells"],
                    "additionalProperties": False,
                },
            },
        }


def plan_cell_batches(
    items: tuple[CellWorkItem, ...],
    *,
    max_items: int = MAX_BATCH_ITEMS,
    max_tokens: int = MAX_BATCH_TOKENS,
    max_formulas: int = MAX_BATCH_FORMULAS,
) -> tuple[CellTranslationBatch, ...]:
    if min(max_items, max_tokens, max_formulas) <= 0:
        raise ValueError("批次限制必须全部大于 0")
    batches: list[CellTranslationBatch] = []
    current: list[CellWorkItem] = []
    token_total = REQUEST_TOKEN_OVERHEAD
    formula_total = 0
    for item in items:
        exceeds = current and (
            len(current) + 1 > max_items
            or token_total + item.estimated_tokens > max_tokens
            or formula_total + item.formula_count > max_formulas
        )
        if exceeds:
            batches.append(CellTranslationBatch(tuple(current)))
            current = []
            token_total = REQUEST_TOKEN_OVERHEAD
            formula_total = 0
        current.append(item)
        token_total += item.estimated_tokens
        formula_total += item.formula_count
    if current:
        batches.append(CellTranslationBatch(tuple(current)))
    return tuple(batches)
```

Allow one individually oversized item to occupy its own batch; segmentation should already have reduced splittable cells.

- [ ] **Step 4: Implement per-ID response parsing**

Add `CellTranslationBatch.parse_response` with these exact outcomes:

```python
def parse_response(self, response: str) -> ParsedBatchResponse:
    try:
        payload = json.loads(response)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TableTranslationError("模型响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise TableTranslationError("模型响应必须是 JSON 对象")
    if set(payload) != {"cells"}:
        raise TableTranslationError("模型响应顶层字段不一致")
    values = payload.get("cells")
    if not isinstance(values, list):
        raise TableTranslationError("模型响应 cells 必须是数组")
    expected = {item.work_id for item in self.items}
    translations: dict[str, str] = {}
    errors: dict[str, str] = {}
    warnings: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            warnings.append("模型结果包含非对象 cell 项")
            continue
        work_id = value.get("cell_id")
        translated = value.get("translated_text")
        if not isinstance(work_id, str) or not work_id:
            warnings.append("模型结果包含无效 cell_id")
            continue
        if work_id not in expected:
            warnings.append(f"模型结果包含未知 cell_id: {work_id}")
            continue
        if work_id in seen:
            translations.pop(work_id, None)
            errors[work_id] = "模型结果存在重复 cell_id"
            continue
        seen.add(work_id)
        if set(value) != {"cell_id", "translated_text"}:
            errors[work_id] = "模型翻译项字段不一致"
            continue
        if not isinstance(translated, str) or not translated.strip():
            errors[work_id] = "模型返回空译文"
            continue
        translations[work_id] = translated.strip()
    for work_id in expected:
        if work_id not in seen:
            errors[work_id] = "模型结果缺少 cell_id"
    return ParsedBatchResponse(translations, errors, tuple(warnings))
```

- [ ] **Step 5: Run table translation tests and commit**

Run:

```bash
pytest -q tests/test_table_translation.py
```

Expected: all tests pass.

Commit:

```bash
git add src/pdf_trans/table_translation.py tests/test_table_translation.py
git commit -m "feat: batch table cells with partial response validation"
```

### Task 4: Adaptive retries, cell fallback, and persisted metadata

**Files:**
- Modify: `src/pdf_trans/translation.py`
- Modify: `tests/test_translation.py`

**Interfaces:**
- Consumes:
  - `prepare_table_translation`
  - `plan_cell_batches`
  - `restore_cell_segment`
  - Task 2/3 records.
- Produces:
  - Extended `TableTranslationOutcome` metadata fields.
  - `_translate_table_one` preserving validated successes across rounds.
  - Persisted `table_translation_partial`, success/fallback counts, and fallback details.

- [ ] **Step 1: Add a translator fixture that records cell batches**

Append to `tests/test_translation.py`:

```python
class CellBatchTranslator:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def translate(self, text, *, response_format=None):
        payload = json.loads(text)
        self.requests.append(payload)
        return self.responder(payload, len(self.requests))


def cell_response(*items):
    return json.dumps(
        {
            "cells": [
                {"cell_id": cell_id, "translated_text": translated}
                for cell_id, translated in items
            ]
        },
        ensure_ascii=False,
    )
```

- [ ] **Step 2: Write failing adaptive-retry and cell-fallback integration tests**

Append:

```python
def test_table_retries_only_failed_cell_and_keeps_successful_formula_cell(
    tmp_path,
):
    formula = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    table = (
        "<table><tr>"
        f"<td>Density {formula}</td>"
        "<td>Corrosion $x$ at $y$</td>"
        "</tr></table>"
    )
    source = tmp_path / "content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table}]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        cells = payload["cells"]
        if call_number == 1:
            return cell_response(
                (cells[0]["cell_id"], "密度 ⟦M0⟧"),
                (cells[1]["cell_id"], "腐蚀 ⟦M0⟧"),
            )
        assert [cell["cell_id"] for cell in cells] == ["cell-0002"]
        return cell_response(("cell-0002", "腐蚀 ⟦M0⟧ 于 ⟦M1⟧"))

    translator = CellBatchTranslator(respond)
    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )
    item = json.loads(output.read_text(encoding="utf-8"))[0]

    assert len(translator.requests) == 2
    assert formula in item["translated_table_body"]
    assert "密度" in item["translated_table_body"]
    assert "腐蚀 $x$ 于 $y$" in item["translated_table_body"]
    assert item["translation_status"] == "success"
    assert item["table_translation_partial"] is False
    assert item["table_translation_success_cell_count"] == 2
    assert item["table_translation_fallback_cell_count"] == 0
    assert item["table_translation_fallbacks"] == []


def test_final_failed_cell_falls_back_without_failing_the_table(tmp_path):
    table = (
        "<table><tr><td>Alpha $x$</td><td>Beta $y$</td></tr></table>"
    )
    source = tmp_path / "content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table}]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        cells = payload["cells"]
        if call_number == 1:
            return cell_response(
                (cells[0]["cell_id"], "甲 ⟦M0⟧"),
                (cells[1]["cell_id"], "乙"),
            )
        return cell_response((cells[0]["cell_id"], "仍然缺少公式"))

    translator = CellBatchTranslator(respond)
    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )
    item = json.loads(output.read_text(encoding="utf-8"))[0]

    assert "甲 $x$" in item["translated_table_body"]
    assert "Beta $y$" in item["translated_table_body"]
    assert item["translation_status"] == "success"
    assert item["table_translation_partial"] is True
    assert item["table_translation_success_cell_count"] == 1
    assert item["table_translation_fallback_cell_count"] == 1
    assert item["table_translation_fallbacks"][0]["cell_id"] == "cell-0002"
    assert "缺失" in item["table_translation_fallbacks"][0]["error"]
```

- [ ] **Step 3: Extend outcome types and implement adaptive rounds**

Change `TableTranslationOutcome` to:

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
    partial: bool = False
    success_cell_count: int = 0
    fallback_cell_count: int = 0
    fallbacks: tuple[CellFallback, ...] = ()
```

Import `CellFallback`, `CellProtectionError`, `plan_cell_batches`, and `restore_cell_segment`. Replace `_translate_table_one`'s whole-table loop with:

```python
pending = {item.work_id: item for item in prepared.work_items}
validated: dict[str, str] = {}
last_errors: dict[str, str] = {}
model_call_count = 0
total_rounds = max_retries + 1

for round_index in range(total_rounds):
    if not pending:
        break
    final_round = round_index == total_rounds - 1
    max_items = 1 if final_round else max(
        1,
        MAX_BATCH_ITEMS // (2 ** round_index),
    )
    batches = plan_cell_batches(
        tuple(pending.values()),
        max_items=max_items,
        max_tokens=MAX_BATCH_TOKENS,
        max_formulas=MAX_BATCH_FORMULAS,
    )
    failed_this_round: dict[str, CellWorkItem] = {}
    for batch_number, batch in enumerate(batches, 1):
        model_call_count += 1
        try:
            response = translator.translate(
                batch.build_request(),
                response_format=batch.build_response_format(),
            )
            if not isinstance(response, str) or not response.strip():
                raise TableTranslationError("模型返回空表格翻译结果")
            parsed = batch.parse_response(response)
        except Exception as exc:
            for item in batch.items:
                failed_this_round[item.work_id] = item
                last_errors[item.work_id] = str(exc)
            LOGGER.warning(
                "第 %d 张表第 %d 轮第 %d 批失败：%s",
                table_number,
                round_index + 1,
                batch_number,
                exc,
            )
            continue
        for warning in parsed.warnings:
            LOGGER.warning(
                "第 %d 张表第 %d 轮第 %d 批：%s",
                table_number,
                round_index + 1,
                batch_number,
                warning,
            )
        batch_items = {item.work_id: item for item in batch.items}
        for work_id, translated_text in parsed.translations.items():
            item = batch_items[work_id]
            segment = prepared.segment_for(work_id)
            try:
                restore_cell_segment(translated_text, segment)
            except CellProtectionError as exc:
                failed_this_round[work_id] = item
                last_errors[work_id] = str(exc)
            else:
                validated[work_id] = translated_text
                last_errors.pop(work_id, None)
        for work_id, error in parsed.errors.items():
            failed_this_round[work_id] = batch_items[work_id]
            last_errors[work_id] = error
    pending = failed_this_round

rebuild = prepared.rebuild(validated, last_errors)
```

Add `PreparedTableTranslation.segment_for(work_id: str) -> ProtectedCellSegment` in Task 2's module and raise `KeyError(work_id)` for unknown IDs. Log each batch with table number, round, batch number, work-item count, accepted count, failed count, and a truncated error summary of at most 200 characters. Never log request text or translated text.

Return a success outcome from `rebuild`; table-level exceptions still use `_failed_table_outcome`. No-work tables return original HTML with all counts zero and no model call.

- [ ] **Step 4: Persist and clear metadata consistently**

Add this module constant in `translation.py`:

```python
_TABLE_METADATA_FIELDS = (
    "table_translation_partial",
    "table_translation_success_cell_count",
    "table_translation_fallback_cell_count",
    "table_translation_fallbacks",
)
```

In `_prepare_new_items`, remove all four fields for table items before setting pending. In `_apply_table_outcome`, write:

```python
item["table_translation_partial"] = outcome.partial
item["table_translation_success_cell_count"] = outcome.success_cell_count
item["table_translation_fallback_cell_count"] = outcome.fallback_cell_count
item["table_translation_fallbacks"] = [
    {"cell_id": fallback.cell_id, "error": fallback.error}
    for fallback in outcome.fallbacks
]
```

On a failed outcome, remove all four fields. In `_restore_table_state`, validate and copy all four fields for an existing success table:

```python
partial = old.get("table_translation_partial", False)
success_count = old.get("table_translation_success_cell_count", 0)
fallback_count = old.get("table_translation_fallback_cell_count", 0)
fallbacks = old.get("table_translation_fallbacks", [])
if (
    not isinstance(partial, bool)
    or type(success_count) is not int
    or success_count < 0
    or type(fallback_count) is not int
    or fallback_count < 0
    or not isinstance(fallbacks, list)
    or len(fallbacks) != fallback_count
    or partial != (fallback_count > 0)
    or any(
        not isinstance(value, dict)
        or not isinstance(value.get("cell_id"), str)
        or not value["cell_id"]
        or not isinstance(value.get("error"), str)
        or not value["error"]
        for value in fallbacks
    )
    or len({value["cell_id"] for value in fallbacks}) != len(fallbacks)
):
    raise TranslationContentError(
        f"断点文件第 {index} 个 success 表格的单元格翻译元数据无效"
    )
```

Copy the validated values to `item`. Legacy success checkpoints without the fields receive `False`, `0`, `0`, and `[]`.

- [ ] **Step 5: Add checkpoint and table-level failure tests**

Append:

```python
def test_resume_preserves_partial_table_metadata(tmp_path):
    source = tmp_path / "content_list.json"
    output = tmp_path / "translated_content_list.json"
    table = "<table><tr><td>Alpha</td></tr></table>"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table}]),
        encoding="utf-8",
    )
    output.write_text(
        json.dumps([{
            "type": "table",
            "table_body": table,
            "translated_table_body": table,
            "translation_status": "success",
            "table_translation_partial": True,
            "table_translation_success_cell_count": 0,
            "table_translation_fallback_cell_count": 1,
            "table_translation_fallbacks": [
                {"cell_id": "cell-0001", "error": "公式占位符缺失"}
            ],
        }]),
        encoding="utf-8",
    )
    translator = CellBatchTranslator(
        lambda payload, call_number: cell_response()
    )

    translate_content_list_file(source, output, translator, concurrency=1)
    item = json.loads(output.read_text(encoding="utf-8"))[0]

    assert translator.requests == []
    assert item["table_translation_partial"] is True
    assert item["table_translation_fallback_cell_count"] == 1


def test_unbalanced_table_is_the_only_kind_of_table_level_failure(tmp_path):
    source = tmp_path / "content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{
            "type": "table",
            "table_body": "<table><tr><td>Alpha</tr></table>",
        }]),
        encoding="utf-8",
    )
    translator = CellBatchTranslator(
        lambda payload, call_number: cell_response()
    )

    translate_content_list_file(source, output, translator, concurrency=1)
    item = json.loads(output.read_text(encoding="utf-8"))[0]

    assert item["translation_status"] == "failed"
    assert "translated_table_body" not in item
    assert "table_translation_partial" not in item
    assert translator.requests == []
```

Use `table_translation_fallback_cell_count` in the checkpoint assertion; this exact field name must match the implementation.

- [ ] **Step 6: Run translation integration tests and commit**

Run:

```bash
pytest -q tests/test_translation.py
```

Expected: all tests pass, including existing ordinary text formula/retry/status tests.

Commit:

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: retry and fall back table cells independently"
```

### Task 5: Long-table regression, complete verification, and plan conformance

**Files:**
- Modify: `tests/test_translation.py`
- Verify only: `src/pdf_trans/renderer.py`
- Verify only: `src/pdf_trans/formula_protection.py`

**Interfaces:**
- Consumes: completed cell-level table translation pipeline.
- Produces: a regression proving the observed long-table failure mode no longer discards a valid density cell.

- [ ] **Step 1: Add a long-table regression modeled on task data**

Append:

```python
def test_long_table_keeps_density_translation_when_later_formula_cell_fails(
    tmp_path,
):
    density_formula = (
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    )
    rows = [
        f"<tr><td>Density at {density_formula}</td><td>775 to 840</td></tr>"
    ]
    rows.extend(
        f"<tr><td>Property {number}</td><td>{number}</td></tr>"
        for number in range(1, 40)
    )
    rows.append(
        "<tr><td>CORROSION Copper strip, "
        r"$2\mathrm{\;h}$ at ${100}^{ \circ }\mathrm{C}$ "
        r"THERMAL STABILITY ${}^{\mathrm{v}}$</td><td>42</td></tr>"
    )
    table = "<table>" + "".join(rows) + "</table>"
    source = tmp_path / "content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table}]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        translated = []
        for cell in payload["cells"]:
            text = cell["text"]
            if "CORROSION" in text:
                translated.append((cell["cell_id"], "腐蚀 ⟦M0⟧"))
            elif "Density" in text:
                translated.append(
                    (cell["cell_id"], "密度在 ⟦M0⟧ 时")
                )
            else:
                translated.append(
                    (cell["cell_id"], text.replace("Property", "性质"))
                )
        return cell_response(*translated)

    translator = CellBatchTranslator(respond)
    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )
    item = json.loads(output.read_text(encoding="utf-8"))[0]

    assert item["translation_status"] == "success"
    assert item["table_translation_partial"] is True
    assert f"密度在 {density_formula} 时" in item["translated_table_body"]
    assert "CORROSION Copper strip" in item["translated_table_body"]
    assert item["table_translation_fallback_cell_count"] == 1
    assert len(translator.requests) >= 2
    assert all(len(request["cells"]) <= 12 for request in translator.requests)
```

- [ ] **Step 2: Run focused regression suites**

Run:

```bash
pytest -q \
  tests/test_table_cell_protection.py \
  tests/test_table_translation.py \
  tests/test_translation.py
```

Expected: all focused tests pass.

- [ ] **Step 3: Verify ordinary text and renderer paths were not changed**

Run:

```bash
git diff b7ec8e1 -- src/pdf_trans/formula_protection.py src/pdf_trans/renderer.py
```

Expected: no output. If either file appears, revert only the new task's edits to that file with `apply_patch`; do not use a destructive git command.

- [ ] **Step 4: Run the complete test suite**

Run:

```bash
pytest -q
```

Expected: the entire suite passes with zero failures.

- [ ] **Step 5: Build the Python package**

Run:

```bash
python -m build
```

Expected: exit code 0 and both an sdist and wheel are generated under `dist/`.

- [ ] **Step 6: Inspect the final diff and status**

Run:

```bash
git diff --check
git status --short
git diff --stat b7ec8e1
```

Expected:

- `git diff --check` prints nothing.
- Status contains only the files named in this plan.
- The diff contains no frontend, MinerU recognition, or renderer changes.

- [ ] **Step 7: Commit the long-table regression**

```bash
git add tests/test_translation.py
git commit -m "test: cover partial fallback in long formula tables"
```

- [ ] **Step 8: Record final verification evidence**

Run:

```bash
git log --oneline b7ec8e1..HEAD
pytest -q
python -m build
```

Expected: the four feature/test commits are listed; pytest and package build both exit 0. Include the test count, build artifact names, branch name, and final commit IDs in the delivery response.
