# Dollar Amount Formula Boundary Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent ordinary dollar amounts and `$...$` candidates crossing HTML tags from being protected as inline LaTeX while preserving the four existing formula boundary families.

**Architecture:** Keep `scan_formula_spans(source)` as the sole boundary decision point. Add small, deterministic predicates used only after a single-dollar candidate is found: one rejects candidates containing an HTML tag, and one rejects numeric-leading candidates with no clear TeX syntax and amount-like text or a following numeric amount. Other delimiters continue through the existing path unchanged.

**Tech Stack:** Python 3.11+, standard-library `re`, dataclasses, and pytest 8.

## Global Constraints

- Ordinary amounts such as `$5`, `$10.50`, `$5 million`, `$10 billion`, `US$5`, `USD $5`, and amount ranges must not become `$...$` spans.
- `$x$`, `$C_7$`, `$x + y = 10$`, `$\\mathrm{C}_{8}$`, and `$$x^2+y^2$$` must remain recognized.
- A single-dollar candidate containing an HTML tag boundary must not be recognized.
- `$$...$$`, `\\(...\\)`, `\\[...\\]`, escaped boundaries, and unclosed boundaries retain current behavior.
- Body protection, table protection, formula audit, and Web Markdown continue consuming the shared scanner; no translation retry, table fallback, or rendering business logic changes.

## File Map

- `src/pdf_trans/formula_scanner.py`: add the two conservative candidate predicates and apply them only to `$...$` candidates in `scan_formula_spans`.
- `tests/test_formula_scanner.py`: cover amount variants, amount ranges, mixed real formulas, and HTML table/cross-tag cases at the unified entry point.
- `tests/test_formula_protection.py`: prove body protection does not hide ordinary money while still hiding a real `$...$` formula beside it.
- `tests/test_table_cell_protection.py`: prove cell-local protection leaves money in separate cells/plain text and protects a real formula.
- `tests/web/test_markdown.py`: prove Markdown rendering preserves money as text and still preserves real formulas; existing four-boundary test remains the cross-caller check.
- `docs/superpowers/plans/2026-08-02-dollar-amount-formula-boundary.md`: records this implementation plan.

### Task 1: Add failing scanner regressions

**Files:**
- Modify: `tests/test_formula_scanner.py`

- [x] Add parametrized assertions that every listed ordinary amount source produces no spans, including two amounts, million/billion phrases, `US$`/`USD $`, and hyphen/en-dash ranges.
- [x] Add a mixed source asserting the amount is ignored while `$x$`, `$C_7$`, `$x + y = 10$`, `$\\mathrm{C}_{8}$`, and `$$x^2+y^2$$` remain in scan order.
- [x] Add a full HTML table source with `$5` and `$10` in different cells and a generic cross-tag source; assert no cross-tag dollar formula is returned.
- [x] Run the new tests and confirm they fail because the current scanner returns `$5 million to $`, range prefixes, or HTML-spanning candidates.

### Task 2: Implement the minimal unified scanner filters

**Files:**
- Modify: `src/pdf_trans/formula_scanner.py`

- [x] Add a compiled HTML-tag pattern and a helper that checks only the source interval between a single-dollar opening and closing boundary.
- [x] Add a numeric-leading amount heuristic that is intentionally conservative: reject a candidate with a numeric first token when it has no clear TeX markers and either contains amount-language/range text, ends at a range separator before another numeric amount, or is otherwise an ambiguous plain numeric phrase.
- [x] Apply these predicates after `_find_closing` and before appending a `FormulaSpan`; do not alter delimiter ordering, escaping, closing selection, span positions, or non-dollar delimiter handling.
- [x] Run scanner tests and confirm the new tests pass.

### Task 3: Verify all callers and regressions

**Files:**
- Modify: `tests/test_formula_protection.py`
- Modify: `tests/test_table_cell_protection.py`
- Modify: `tests/web/test_markdown.py`

- [x] Add caller-level checks that ordinary money is not replaced by placeholders and a neighboring real formula is still protected/rendered.
- [x] Run the focused scanner, body, table, translation, audit, and Web Markdown suites.
- [x] Run the complete pytest suite and inspect the final diff to ensure only the requested scanner behavior and regression tests changed.
