# Unified Formula Boundary Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make body translation protection, HTML table-cell translation protection, formula auditing/replacement, and Web Markdown rendering use one scanner for `$...$`, `$$...$$`, `\(...\)`, and `\[...\]`.

**Architecture:** Promote the existing rich span model in `formula_scanner.py` to the public `FormulaSpan` type and expose `scan_formula_spans(source)`. Keep each caller's placeholder protocol and restoration validation unchanged, but replace every private boundary parser with the shared scanner so boundary priority, escaping, completeness, and source positions have one definition.

**Tech Stack:** Python 3.11+, standard-library `dataclasses`, `hashlib`, `html`, `re`, and `secrets`; pytest 8; existing Markdown-It and nh3 Web dependencies.

## Global Constraints

- Supported boundaries are exactly `$...$`, `$$...$$`, `\(...\)`, and `\[...\]`.
- Escaped boundaries must not be recognized; unclosed boundaries must remain ordinary text.
- Existing `$...$` and `$$...$$` behavior must not regress.
- Restored formulas must match their source text character-for-character.
- Missing, added, duplicated, tampered, or reordered formula placeholders must still fail restoration.
- Do not change the body, table-cell, or Markdown placeholder formats or their existing hash/order validation rules.
- Do not address ordinary amounts such as `$5` and `$10`, and do not change unrelated business logic.

---

## File Map

- `src/pdf_trans/formula_scanner.py`: owns `FormulaSpan` and the only formula-boundary scanner, and continues to own audit/table span adaptation and replacement.
- `src/pdf_trans/formula_protection.py`: consumes shared spans for body protection and restoration validation.
- `src/pdf_trans/table_cell_protection.py`: consumes shared spans for cell-local markers and restoration validation.
- `src/pdf_trans/web/markdown.py`: consumes shared spans before Markdown parsing; retains its existing Markdown-specific placeholder/HTML-escape logic.
- `tests/test_formula_scanner.py`: specifies the shared scanner interface and escape/incomplete behavior.
- `tests/test_formula_protection.py`: specifies body protection/restoration and invalid response handling for all four boundaries.
- `tests/test_table_cell_protection.py`: specifies cell-local protection/restoration and invalid marker handling for all four boundaries.
- `tests/test_table_translation.py`: verifies the full HTML table-cell preparation and rebuild path.
- `tests/web/test_markdown.py`: remains the behavior-level regression suite for the Markdown consumer.

### Task 1: Publish the shared formula span scanner

**Files:**
- Modify: `tests/test_formula_scanner.py:5-12,123-145`
- Modify: `src/pdf_trans/formula_scanner.py:13-18,60-68,71-77,117-187,316-341,373-383,440-465,492-533`

**Interfaces:**
- Consumes: Existing delimiter ordering and escape/closing rules in `formula_scanner.py`.
- Produces: `FormulaSpan` and `scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]` for Tasks 2–4.

- [ ] **Step 1: Add failing public scanner tests**

Extend the import in `tests/test_formula_scanner.py` and add these tests after the existing frontend delimiter test:

```python
from pdf_trans.formula_scanner import (
    FormulaSpan,
    rebuild_raw_formula,
    replace_equation_formula,
    replace_formula_spans,
    replace_table_formula_spans,
    scan_content_list,
    scan_formula_spans,
)


def test_public_scanner_returns_all_supported_formula_spans():
    source = r"a $x$ b $$y$$ c \(z\) d \[w\]"

    spans = scan_formula_spans(source)

    assert all(isinstance(span, FormulaSpan) for span in spans)
    assert [span.raw_formula for span in spans] == [
        "$x$",
        "$$y$$",
        r"\(z\)",
        r"\[w\]",
    ]
    assert [span.katex_formula for span in spans] == ["x", "y", "z", "w"]
    assert [span.is_block for span in spans] == [False, True, False, True]
    assert [source[span.start : span.end] for span in spans] == [
        span.raw_formula for span in spans
    ]
    assert [source[span.katex_start : span.katex_end] for span in spans] == [
        span.katex_formula for span in spans
    ]


@pytest.mark.parametrize(
    "source",
    [
        r"\$x\$",
        r"\$\$x\$\$",
        r"\\(x\\)",
        r"\\[x\\]",
        "$x",
        "$$x",
        r"\(x",
        r"\[x",
    ],
)
def test_public_scanner_treats_escaped_and_unclosed_boundaries_as_text(source):
    assert scan_formula_spans(source) == ()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/pytest \
  tests/test_formula_scanner.py::test_public_scanner_returns_all_supported_formula_spans \
  tests/test_formula_scanner.py::test_public_scanner_treats_escaped_and_unclosed_boundaries_as_text \
  -v
```

Expected: collection fails because `FormulaSpan` and `scan_formula_spans` are not public names yet.

- [ ] **Step 3: Promote the existing span model and scanner**

In `src/pdf_trans/formula_scanner.py`, make the existing span type and function public without changing their algorithm:

```python
@dataclass(frozen=True)
class FormulaSpan:
    raw_formula: str
    katex_formula: str
    is_block: bool
    start: int
    end: int
    katex_start: int
    katex_end: int


@dataclass(frozen=True)
class _TableFormula:
    row_idx: int
    col_idx: int
    cell_tag: str
    formula_index: int
    span: FormulaSpan
```

Rename `_formula_spans` to `scan_formula_spans` and instantiate `FormulaSpan`:

```python
def scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]:
    spans: list[FormulaSpan] = []
    position = 0
    while position < len(source):
        matched = False
        for opening, closing, is_block in DELIMITERS:
            if not source.startswith(opening, position):
                continue
            if _is_escaped(source, position):
                continue
            if opening == "$" and source.startswith("$$", position):
                continue
            end = _find_closing(
                source,
                position + len(opening),
                closing,
            )
            if end is None:
                continue
            raw_end = end + len(closing)
            spans.append(
                FormulaSpan(
                    raw_formula=source[position:raw_end],
                    katex_formula=source[
                        position + len(opening) : end
                    ],
                    is_block=is_block,
                    start=position,
                    end=raw_end,
                    katex_start=position + len(opening),
                    katex_end=end,
                )
            )
            position = raw_end
            matched = True
            break
        if not matched:
            position += 1
    return tuple(spans)
```

Apply these identifier-only replacements throughout `formula_scanner.py`; do not alter the bodies of `_equation_formula`, `_candidate`, or `_replace_spans`:

```diff
-class _FormulaSpan:
+class FormulaSpan:

-    span: _FormulaSpan
+    span: FormulaSpan

-def _formula_spans(source: str) -> tuple[_FormulaSpan, ...]:
-    spans: list[_FormulaSpan] = []
+def scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]:
+    spans: list[FormulaSpan] = []

-                _FormulaSpan(
+                FormulaSpan(

-def _equation_formula(source: str) -> _FormulaSpan:
+def _equation_formula(source: str) -> FormulaSpan:

-            return _FormulaSpan(
+            return FormulaSpan(

-    return _FormulaSpan(
+    return FormulaSpan(

-    span: _FormulaSpan,
+    span: FormulaSpan,

-    spans: tuple[_FormulaSpan, ...],
+    spans: tuple[FormulaSpan, ...],

-        for span in _formula_spans(data):
+        for span in scan_formula_spans(data):

-        _formula_spans(source),
+        scan_formula_spans(source),

-                _formula_spans(text)
+                scan_formula_spans(text)
```

- [ ] **Step 4: Run scanner tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_formula_scanner.py -v
```

Expected: all scanner tests pass, including existing `$`/`$$`, table HTML, replacement, escaped, and unclosed cases.

- [ ] **Step 5: Commit the public scanner API**

```bash
git add src/pdf_trans/formula_scanner.py tests/test_formula_scanner.py
git commit -m "refactor: expose shared formula span scanner"
```

### Task 2: Route body translation protection through the shared scanner

**Files:**
- Modify: `tests/test_formula_protection.py:14-120`
- Modify: `src/pdf_trans/formula_protection.py:3-7,33-78,90-117,152-173`

**Interfaces:**
- Consumes: `scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]` from Task 1.
- Produces: Existing `FormulaProtectionContext.protect()` and `.restore()` behavior extended to all four formula boundaries.

- [ ] **Step 1: Add failing all-boundary body protection tests**

Add these tests to `tests/test_formula_protection.py`:

```python
def test_protects_and_restores_all_supported_formula_boundaries_exactly():
    source = r"Inline $a_b$, block $$c_d$$, paren \(e_f\), bracket \[g_h\]."
    context = FormulaProtectionContext(nonce="0123456789abcdef")

    protected = context.protect(source)

    assert len(PLACEHOLDER_RE.findall(protected.model_text)) == 4
    assert all(
        formula not in protected.model_text
        for formula in ("$a_b$", "$$c_d$$", r"\(e_f\)", r"\[g_h\]")
    )
    translated = protected.model_text.replace("Inline", "行内").replace(
        "block", "块级"
    )
    assert context.restore(translated, protected) == source.replace(
        "Inline", "行内"
    ).replace("block", "块级")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda text, values: text.replace(values[0], "", 1), "缺失"),
        (
            lambda text, values: text.replace(
                values[0],
                values[0].replace(
                    "0123456789abcdef", "fedcba9876543210"
                ),
                1,
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
def test_rejects_invalid_backslash_formula_placeholders(mutate, message):
    context = FormulaProtectionContext(nonce="0123456789abcdef")
    protected = context.protect(r"Left \(x\) middle \[y\] right")
    placeholders = PLACEHOLDER_RE.findall(protected.model_text)

    assert len(placeholders) == 2
    with pytest.raises(FormulaProtectionError, match=message):
        context.restore(mutate(protected.model_text, placeholders), protected)


@pytest.mark.parametrize(
    "source",
    [
        r"escaped \$x\$",
        r"escaped \$\$x\$\$",
        r"escaped \\(x\\)",
        r"escaped \\[x\\]",
        "unclosed $x",
        "unclosed $$x",
        r"unclosed \(x",
        r"unclosed \[x",
    ],
)
def test_body_protection_leaves_escaped_and_unclosed_boundaries_as_text(source):
    context = FormulaProtectionContext()

    protected = context.protect(source)

    assert protected.model_text == source
    assert protected.formula_ids == ()
    assert context.restore(source, protected) == source
```

- [ ] **Step 2: Run the new body tests and verify RED**

Run:

```bash
.venv/bin/pytest \
  tests/test_formula_protection.py::test_protects_and_restores_all_supported_formula_boundaries_exactly \
  tests/test_formula_protection.py::test_rejects_invalid_backslash_formula_placeholders \
  tests/test_formula_protection.py::test_body_protection_leaves_escaped_and_unclosed_boundaries_as_text \
  -v
```

Expected: the all-boundary test sees only two placeholders, and the backslash placeholder test cannot obtain two placeholders.

- [ ] **Step 3: Replace the private body scanner with the shared scanner**

In `src/pdf_trans/formula_protection.py`, add the shared import and remove `_is_escaped`, `_find_closing`, and `_formula_spans`:

```python
from pdf_trans.formula_scanner import scan_formula_spans
```

Define extraction from the public span records:

```python
def _extract_formulas(text: str) -> tuple[str, ...]:
    return tuple(span.raw_formula for span in scan_formula_spans(text))
```

Change the protection loop while preserving the placeholder, ID, digest, and record logic:

```python
        parts: list[str] = []
        formula_ids: list[str] = []
        cursor = 0
        for span in scan_formula_spans(text):
            parts.append(text[cursor : span.start])
            original = span.raw_formula
            formula_id = f"{len(self._records) + 1:04d}"
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
            placeholder = (
                f"⟪{_SENTINEL_MARKER}:{self._nonce}:{formula_id}:{digest}⟫"
            )
            self._records[formula_id] = _FormulaRecord(
                formula_id=formula_id,
                original=original,
                digest=digest,
                placeholder=placeholder,
            )
            formula_ids.append(formula_id)
            parts.append(placeholder)
            cursor = span.end
        parts.append(text[cursor:])
```

Do not change the rest of `restore`: its existing missing, duplicate, added, tampered, order, digest, exact restoration, and sentinel checks will now operate on all four boundaries through `_extract_formulas`.

- [ ] **Step 4: Run body protection tests and verify GREEN**

Run:

```bash
.venv/bin/pytest tests/test_formula_protection.py -v
```

Expected: all body protection tests pass, including legacy dollar behavior and all invalid placeholder cases.

- [ ] **Step 5: Commit body protection reuse**

```bash
git add src/pdf_trans/formula_protection.py tests/test_formula_protection.py
git commit -m "fix: protect all formula boundaries in body text"
```

### Task 3: Route table-cell protection through the shared scanner

**Files:**
- Modify: `tests/test_table_cell_protection.py:25-48,62-102`
- Modify: `tests/test_table_translation.py:48-69,85-118`
- Modify: `src/pdf_trans/table_cell_protection.py:3-7,42-87,142-175,198-230`

**Interfaces:**
- Consumes: `scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]` from Task 1.
- Produces: Existing `protect_cell_text()` and `restore_cell_segment()` behavior extended to all four boundaries, including the full `prepare_table_translation()`/`PreparedTable.rebuild()` path.

- [ ] **Step 1: Add failing cell-level protection and validation tests**

Add these tests to `tests/test_table_cell_protection.py`:

```python
def test_protects_and_restores_all_supported_cell_formula_boundaries_exactly():
    source = r"Alpha $a$ beta $$b$$ gamma \(c\) delta \[d\]"

    protected = protect_cell_text(source)

    assert protected.model_text == (
        "Alpha ⟦M0⟧ beta ⟦M1⟧ gamma ⟦M2⟧ delta ⟦M3⟧"
    )
    assert [value.original for value in protected.formula_markers] == [
        "$a$",
        "$$b$$",
        r"\(c\)",
        r"\[d\]",
    ]
    assert restore_cell_segment(
        "甲 ⟦M0⟧ 乙 ⟦M1⟧ 丙 ⟦M2⟧ 丁 ⟦M3⟧",
        protected,
    ) == r"甲 $a$ 乙 $$b$$ 丙 \(c\) 丁 \[d\]"


@pytest.mark.parametrize(
    ("translated", "message"),
    [
        ("甲 ⟦M0⟧", "缺失"),
        ("甲 ⟦M-0⟧ 乙 ⟦M1⟧", "篡改"),
        ("甲 ⟦M1⟧ 乙 ⟦M0⟧", "顺序"),
    ],
)
def test_rejects_invalid_backslash_formula_cell_markers(translated, message):
    protected = protect_cell_text(r"Alpha \(x\) Beta \[y\]")

    assert len(protected.formula_markers) == 2
    with pytest.raises(CellProtectionError, match=message):
        restore_cell_segment(translated, protected)


@pytest.mark.parametrize(
    "source",
    [
        r"escaped \$x\$",
        r"escaped \$\$x\$\$",
        r"escaped \\(x\\)",
        r"escaped \\[x\\]",
        "unclosed $x",
        "unclosed $$x",
        r"unclosed \(x",
        r"unclosed \[x",
    ],
)
def test_cell_protection_leaves_escaped_and_unclosed_boundaries_as_text(source):
    protected = protect_cell_text(source)

    assert protected.model_text == source
    assert protected.formula_markers == ()
    assert restore_cell_segment(source, protected) == source
```

- [ ] **Step 2: Add a failing full HTML table-cell round-trip test**

Add this test to `tests/test_table_translation.py` after `test_formula_ids_restart_in_each_cell_and_latex_is_hidden`:

```python
def test_html_table_cell_protects_and_restores_all_formula_boundaries():
    source = (
        r"<table><tr><td>Values $a$, $$b$$, \(c\), and \[d\].</td>"
        r"</tr></table>"
    )

    prepared = prepare_table_translation(source)

    assert [work.model_text for work in prepared.work_items] == [
        "Values ⟦M0⟧, ⟦M1⟧, ⟦M2⟧, and ⟦M3⟧."
    ]
    result = prepared.rebuild(
        {"cell-0001": "值 ⟦M0⟧、⟦M1⟧、⟦M2⟧ 和 ⟦M3⟧。"},
        {},
    )

    assert result.translated_html == (
        r"<table><tr><td>值 $a$、$$b$$、\(c\) 和 \[d\]。</td>"
        r"</tr></table>"
    )
    assert result.success_cell_count == 1
    assert result.fallback_cell_count == 0
```

- [ ] **Step 3: Run the new table tests and verify RED**

Run:

```bash
.venv/bin/pytest \
  tests/test_table_cell_protection.py::test_protects_and_restores_all_supported_cell_formula_boundaries_exactly \
  tests/test_table_cell_protection.py::test_rejects_invalid_backslash_formula_cell_markers \
  tests/test_table_cell_protection.py::test_cell_protection_leaves_escaped_and_unclosed_boundaries_as_text \
  tests/test_table_translation.py::test_html_table_cell_protects_and_restores_all_formula_boundaries \
  -v
```

Expected: only dollar formulas are replaced, so four-marker assertions and backslash marker validation fail.

- [ ] **Step 4: Replace the private cell scanner with the shared scanner**

In `src/pdf_trans/table_cell_protection.py`, add the shared import and remove `_is_escaped`, `_find_closing`, and `_formula_spans`:

```python
from pdf_trans.formula_scanner import scan_formula_spans
```

Define extraction from the public spans:

```python
def _extract_formulas(text: str) -> tuple[str, ...]:
    return tuple(span.raw_formula for span in scan_formula_spans(text))
```

Change `protect_cell_text` to consume rich spans while preserving marker IDs, hashes, and HTML marker handling:

```python
    pieces: list[str] = []
    markers: list[CellMarker] = []
    cursor = 0
    for number, span in enumerate(scan_formula_spans(text)):
        original = span.raw_formula
        marker_id = f"M{number}"
        placeholder = f"⟦{marker_id}⟧"
        pieces.extend((text[cursor : span.start], placeholder))
        markers.append(
            CellMarker(
                marker_id=marker_id,
                placeholder=placeholder,
                original=original,
                sha256=_digest(original),
            )
        )
        cursor = span.end
    pieces.append(text[cursor:])
```

Keep the existing raw-dollar precheck in `restore_cell_segment` unchanged. The final `_extract_formulas(restored)` comparison will now also reject complete model-added `\(...\)` or `\[...\]` formulas and verify exact restoration for every supported boundary.

- [ ] **Step 5: Run table protection and translation tests and verify GREEN**

Run:

```bash
.venv/bin/pytest \
  tests/test_table_cell_protection.py \
  tests/test_table_translation.py \
  -v
```

Expected: all cell-level and full HTML table translation tests pass.

- [ ] **Step 6: Commit table-cell protection reuse**

```bash
git add \
  src/pdf_trans/table_cell_protection.py \
  tests/test_table_cell_protection.py \
  tests/test_table_translation.py
git commit -m "fix: protect all formula boundaries in table cells"
```

### Task 4: Route Markdown protection through the shared scanner

**Files:**
- Modify: `src/pdf_trans/web/markdown.py:3-18,64-144`
- Verify: `tests/web/test_markdown.py`

**Interfaces:**
- Consumes: `scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]` from Task 1.
- Produces: `render_safe_markdown()` with unchanged output and no private formula boundary parser.

- [ ] **Step 1: Establish the current Markdown behavior baseline**

Run before refactoring:

```bash
.venv/bin/pytest tests/web/test_markdown.py -v
```

Expected: all existing Markdown tests pass, including all four delimiters, escaped/unclosed text, HTML escaping, emphasis isolation, sanitization, and URL rewriting.

- [ ] **Step 2: Remove the Markdown-specific scanner and consume shared spans**

In `src/pdf_trans/web/markdown.py`, add:

```python
from pdf_trans.formula_scanner import scan_formula_spans
```

Delete `_MATH_DELIMITERS`, `_is_escaped`, `_opening_at`, and `_find_closing`. Replace `_protect_math` with:

```python
def _protect_math(
    source: str,
) -> tuple[str, tuple[_ProtectedMath, ...]]:
    nonce = secrets.token_hex(16).upper()
    parts: list[str] = []
    records: list[_ProtectedMath] = []
    copied_until = 0

    for span in scan_formula_spans(source):
        placeholder = (
            f"PDFTRANSMATH{nonce}{len(records):08d}TOKEN"
        )
        parts.append(source[copied_until : span.start])
        parts.append(placeholder)
        records.append(
            _ProtectedMath(
                placeholder=placeholder,
                source=span.raw_formula,
            )
        )
        copied_until = span.end

    parts.append(source[copied_until:])
    return "".join(parts), tuple(records)
```

Leave `_restore_math`, HTML escaping, placeholder quantity/order checks, Markdown-It rendering, URL rewriting, and nh3 cleaning unchanged.

- [ ] **Step 3: Run Markdown and scanner regression tests**

Run:

```bash
.venv/bin/pytest tests/web/test_markdown.py tests/test_formula_scanner.py -v
```

Expected: all tests pass with output behavior unchanged.

- [ ] **Step 4: Confirm there is only one boundary parser**

Run:

```bash
rg -n "def (_formula_spans|_opening_at)|_MATH_DELIMITERS" \
  src/pdf_trans/formula_protection.py \
  src/pdf_trans/table_cell_protection.py \
  src/pdf_trans/web/markdown.py
rg -n "scan_formula_spans" \
  src/pdf_trans/formula_scanner.py \
  src/pdf_trans/formula_protection.py \
  src/pdf_trans/table_cell_protection.py \
  src/pdf_trans/web/markdown.py
```

Expected: the first command prints no matches; the second prints the public definition and all three external consumers.

- [ ] **Step 5: Commit Markdown scanner reuse**

```bash
git add src/pdf_trans/web/markdown.py
git commit -m "refactor: reuse shared formula scanner in markdown"
```

### Task 5: Run focused and full verification

**Files:**
- Verify: `src/pdf_trans/formula_scanner.py`
- Verify: `src/pdf_trans/formula_protection.py`
- Verify: `src/pdf_trans/table_cell_protection.py`
- Verify: `src/pdf_trans/web/markdown.py`
- Verify: `tests/test_formula_scanner.py`
- Verify: `tests/test_formula_protection.py`
- Verify: `tests/test_table_cell_protection.py`
- Verify: `tests/test_table_translation.py`
- Verify: `tests/web/test_markdown.py`

**Interfaces:**
- Consumes: All implementations and tests completed in Tasks 1–4.
- Produces: Fresh evidence that the requested behavior works and unrelated tests did not regress.

- [ ] **Step 1: Run all formula and table regressions together**

```bash
.venv/bin/pytest \
  tests/test_formula_scanner.py \
  tests/test_formula_protection.py \
  tests/test_table_cell_protection.py \
  tests/test_table_translation.py \
  tests/web/test_markdown.py \
  -v
```

Expected: zero failures.

- [ ] **Step 2: Run the complete test suite**

```bash
.venv/bin/pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Build the package**

```bash
.venv/bin/python -m build
```

Expected: exit code 0 and fresh wheel/source archives under `dist/`.

- [ ] **Step 4: Check the final diff and scope**

```bash
git diff --check HEAD~4..HEAD
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: no whitespace errors; only the four implementation files and their scoped tests differ from the implementation starting point; build archives remain ignored or otherwise untracked state is reported explicitly.

- [ ] **Step 5: Record verification without an extra code commit**

Report the exact focused-test count, full-suite count, and build result. Do not create a verification-only commit when no files changed.
