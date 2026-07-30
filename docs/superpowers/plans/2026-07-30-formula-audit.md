# MinerU Formula Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only formula audit that scans the original MinerU content list, batch-validates formulas with the frontend's vendored KaTeX, reports syntax and suspicious-content findings, and runs before Web translation and Web resume.

**Architecture:** Focused Python modules discover and locate formulas, classify suspicious patterns, invoke one Node subprocess for a complete KaTeX batch, and atomically write a versioned report. The full workflow audits immediately after MinerU extraction; Web resume validates and reuses the report or backfills it for legacy checkpoints without changing translation, Markdown, or frontend rendering code.

**Tech Stack:** Python 3.11+, standard-library `dataclasses`, `html.parser`, `hashlib`, `json`, `subprocess`, Node.js, vendored KaTeX 0.18.1, pytest 8.

## Global Constraints

- Scan the original MinerU `*_content_list.json`, not cleaned, normalized, or translated content.
- Scan `type=equation`, delimited formulas in string `text` fields, and formulas in every `td`/`th` of `table_body`.
- Keep original `text`, `table_body`, and source-file bytes unchanged.
- Start at most one Node process per non-empty audit; never one process per formula.
- Load the existing vendored KaTeX 0.18.1 and use `throwOnError:false`, `trust:false`, `maxSize:20`, and `maxExpand:500`.
- Formula findings never block translation; audit infrastructure failures do.
- Suspicious rules annotate only and never replace formula content.
- `FormulaNormalizer` is a no-op in version 1; `normalized_formula`, `normalization_rule`, and `confidence` stay `null`.
- Do not change translation behavior, Markdown generation, or the frontend renderer.
- Do not add a `--translate-only` audit entry point.

## File Structure

- Create `src/pdf_trans/formula_scanner.py`: delimiter scanning, equation discovery, HTML cell traversal, visual-grid positions, hashes, and stable IDs.
- Create `src/pdf_trans/formula_rules.py`: suspicious-rule matches and the no-op `FormulaNormalizer` interface.
- Create `src/pdf_trans/formula_validation.py`: `FormulaValidator`, validation DTOs, protocol checks, and the one-process KaTeX adapter.
- Create `src/pdf_trans/katex_validator.js`: stdin/stdout JSON batch protocol around the vendored KaTeX bundle.
- Create `src/pdf_trans/formula_audit.py`: file orchestration, record classification, report validation, atomic output, and aggregate logging.
- Modify `src/pdf_trans/errors.py`: add the expected application-level `FormulaAuditError`.
- Modify `src/pdf_trans/workflow.py`: run the audit after extraction and before cleaning.
- Modify `src/pdf_trans/web/task_runner.py`: reuse or backfill the report before a normalized checkpoint resumes.
- Modify `pyproject.toml`: package the Node validator script.
- Modify `Dockerfile`: install the Node runtime required by Web production.
- Modify `README.md`: document the new output stage and report semantics.
- Create `tests/test_formula_scanner.py`, `tests/test_formula_rules.py`, `tests/test_formula_validation.py`, and `tests/test_formula_audit.py`.
- Modify `tests/test_workflow.py`, `tests/web/test_task_runner.py`, and `tests/test_deployment_config.py`.

---

### Task 1: Formula Discovery, Source Locations, and Stable IDs

**Files:**
- Create: `src/pdf_trans/formula_scanner.py`
- Create: `tests/test_formula_scanner.py`
- Modify: `src/pdf_trans/errors.py:1-30`

**Interfaces:**
- Consumes: a parsed `list[object]` from the original content-list JSON.
- Produces: `FormulaCandidate` and `scan_content_list(items: list[object]) -> tuple[FormulaCandidate, ...]`.
- Produces: `FormulaAuditError` for malformed audit input such as invalid table HTML.

- [ ] **Step 1: Write failing tests for all source types and stable locations**

Create tests that exercise equation, inline/block delimiter, and table-cell
discovery through the intended public API:

```python
from copy import deepcopy

from pdf_trans.formula_scanner import scan_content_list


def test_scans_equations_and_all_frontend_text_delimiters_without_mutation():
    items = [
        {
            "type": "equation",
            "page_idx": 2,
            "bbox": [1, 2, 3, 4],
            "text": "$$\nC_7 \\neqq C_6\n$$",
        },
        {
            "type": "text",
            "page_idx": 3,
            "bbox": [5, 6, 7, 8],
            "text": "a $x$ b $$y$$ c \\(z\\) d \\[w\\]",
        },
    ]
    original = deepcopy(items)

    formulas = scan_content_list(items)

    assert [value.raw_formula for value in formulas] == [
        "$$\nC_7 \\neqq C_6\n$$",
        "$x$",
        "$$y$$",
        "\\(z\\)",
        "\\[w\\]",
    ]
    assert [value.katex_formula for value in formulas] == [
        "\nC_7 \\neqq C_6\n",
        "x",
        "y",
        "z",
        "w",
    ]
    assert [value.is_block for value in formulas] == [
        True,
        False,
        True,
        False,
        True,
    ]
    assert formulas[0].field_path == "/0/text"
    assert formulas[1].field_path == "/1/text"
    assert items == original


def test_scans_multiple_formulas_in_table_cell_and_expands_spans():
    items = [
        {
            "type": "table",
            "page_idx": 7,
            "bbox": [10, 20, 30, 40],
            "table_body": (
                "<table>"
                "<tr><th rowspan=\"2\">Name</th><th colspan=\"2\">Values</th></tr>"
                "<tr><td>$x$ and $$y$$</td><td>\\(z\\)</td></tr>"
                "</table>"
            ),
        }
    ]

    formulas = scan_content_list(items)

    assert [value.raw_formula for value in formulas] == ["$x$", "$$y$$", "\\(z\\)"]
    assert [(value.table_row_idx, value.table_col_idx) for value in formulas] == [
        (1, 1),
        (1, 1),
        (1, 2),
    ]
    assert [value.formula_index for value in formulas] == [0, 1, 0]
    assert all(value.field_path == "/0/table_body" for value in formulas)
    assert all(value.source_type == "table_cell" for value in formulas)


def test_ids_are_repeatable_but_include_location():
    items = [
        {"type": "text", "page_idx": 0, "text": "$x$ and $x$"},
    ]

    first = scan_content_list(items)
    second = scan_content_list(deepcopy(items))

    assert [value.formula_id for value in first] == [
        value.formula_id for value in second
    ]
    assert first[0].formula_id != first[1].formula_id
    assert first[0].content_hash == first[1].content_hash
```

Add focused tests for escaped delimiters, unclosed delimiters, formulas in
separate table text nodes, `td` and `th`, malformed table HTML, and
non-object/non-string fields.

- [ ] **Step 2: Run the scanner tests and confirm RED**

Run:

```bash
python -m pytest tests/test_formula_scanner.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'pdf_trans.formula_scanner'`.

- [ ] **Step 3: Add the audit exception and immutable candidate model**

Append to `src/pdf_trans/errors.py`:

```python
class FormulaAuditError(PDFTransError):
    """Raised when a trustworthy formula audit cannot be produced."""
```

Create the scanner's public model exactly as:

```python
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class FormulaCandidate:
    formula_id: str
    page_idx: Any
    bbox: Any
    source_type: Literal["equation", "text", "table_cell"]
    field_path: str
    table_row_idx: int | None
    table_col_idx: int | None
    cell_tag: str | None
    formula_index: int
    raw_formula: str
    katex_formula: str
    is_block: bool
    content_hash: str
```

- [ ] **Step 4: Implement deterministic delimiter and equation extraction**

Implement a private `_FormulaSpan`, `_formula_spans(source)`, and
`_equation_formula(source)`. Use this exact escape test:

```python
def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1
```

Use opening/closing pairs in this precedence order:

```python
DELIMITERS = (
    ("$$", "$$", True),
    ("\\[", "\\]", True),
    ("\\(", "\\)", False),
    ("$", "$", False),
)
```

`_formula_spans` must:

- skip escaped openings and closings;
- prevent `$` from consuming either half of `$$`;
- preserve the exact delimited `raw_formula`;
- return only complete pairs;
- return the delimiter-free `katex_formula`.

`_equation_formula` must keep the exact input as `raw_formula`, remove one
matching outer `$$...$$` or `\[...\]` pair from a whitespace-trimmed copy for
KaTeX, and otherwise validate the complete source as display math.

- [ ] **Step 5: Implement table parsing with visual-grid positions**

Use `html.parser.HTMLParser(convert_charrefs=False)`. Track:

- a single table nesting level;
- the current zero-based `tr`;
- a column cursor;
- `occupied_until: dict[int, int]` for row spans;
- the active `td`/`th`;
- hidden `script`, `style`, and `template` nesting.

At each cell start:

```python
while occupied_until.get(column, 0) > row_idx:
    column += 1
cell_column = column
rowspan = _positive_span(attrs, "rowspan")
colspan = _positive_span(attrs, "colspan")
for occupied_column in range(cell_column, cell_column + colspan):
    occupied_until[occupied_column] = max(
        occupied_until.get(occupied_column, 0),
        row_idx + rowspan,
    )
column = cell_column + colspan
```

Scan each visible `handle_data` value independently and increment
`formula_index` across all text nodes in the same cell. Reject unclosed,
mismatched, nested cell, and invalid span markup with `FormulaAuditError` that
includes the `/N/table_body` field path.

- [ ] **Step 6: Implement stable hashes and the top-level scanner**

Build each candidate through one helper. Hash exact source text:

```python
content_hash = hashlib.sha256(raw_formula.encode("utf-8")).hexdigest()
identity = {
    "page_idx": page_idx,
    "bbox": bbox,
    "source_type": source_type,
    "field_path": field_path,
    "table_row_idx": table_row_idx,
    "table_col_idx": table_col_idx,
    "formula_index": formula_index,
    "content_hash": content_hash,
}
encoded = json.dumps(
    identity,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
formula_id = "formula_" + hashlib.sha256(encoded).hexdigest()[:24]
```

Implement the public signature
`scan_content_list(items: list[object]) -> tuple[FormulaCandidate, ...]`.

For `type=equation`, emit the whole `text` once and do not rescan it as a
delimited text field. For every other object with string `text`, scan its
delimiters. Independently scan string `table_body` for `td`/`th` formulas.
Ignore non-object array members and non-string fields without mutation.

- [ ] **Step 7: Run scanner tests and the existing formula-protection tests**

Run:

```bash
python -m pytest \
  tests/test_formula_scanner.py \
  tests/test_formula_protection.py \
  tests/test_table_cell_protection.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit the scanner**

```bash
git add \
  src/pdf_trans/errors.py \
  src/pdf_trans/formula_scanner.py \
  tests/test_formula_scanner.py
git commit -m "feat: discover and locate MinerU formulas"
```

---

### Task 2: Suspicious Rules and No-Op Normalizer

**Files:**
- Create: `src/pdf_trans/formula_rules.py`
- Create: `tests/test_formula_rules.py`

**Interfaces:**
- Consumes: delimiter-free LaTeX strings from `FormulaCandidate.katex_formula`.
- Produces: `SuspicionMatch`, `find_suspicious_formula(formula: str)`, `FormulaNormalizer`, `NormalizationResult`, and `NoOpFormulaNormalizer`.

- [ ] **Step 1: Write failing tests for every required MinerU pattern**

Create parameterized tests with exact rule IDs:

```python
import pytest

from pdf_trans.formula_rules import (
    NormalizationResult,
    NoOpFormulaNormalizer,
    find_suspicious_formula,
)


@pytest.mark.parametrize(
    ("formula", "rule"),
    [
        ("C_7 \\neqq C_6", "mineru_neqq"),
        ("C_4 \\complement C_3", "mineru_complement"),
        ("\\text{Meter Ma \\max}", "max_inside_text"),
        ("Meter Ma \\max", "split_meter_max"),
        ("Meter Ma ^{\\max}", "split_meter_max"),
        ("F I C / H I C", "split_instrument_tag"),
        ("\\frac{m 3}{h}", "split_cubic_meter_fraction"),
    ],
)
def test_marks_known_mineru_corruptions(formula, rule):
    assert rule in {
        match.rule for match in find_suspicious_formula(formula)
    }


def test_genuine_neq_is_not_marked_or_normalized():
    raw = "C_7 \\neq C_6"

    assert find_suspicious_formula(raw) == ()
    assert NoOpFormulaNormalizer().normalize(raw) == NormalizationResult(
        normalized_formula=None,
        normalization_rule=None,
        confidence=None,
    )
    assert raw == "C_7 \\neq C_6"
```

Also test flexible whitespace for each spaced pattern, an ordinary
`\\text{maximum}` without `\\max`, command-name boundaries such as
`\\neqquality`, and a balanced nested `\\text{a {b} \\max}` group.

- [ ] **Step 2: Run rule tests and confirm RED**

Run:

```bash
python -m pytest tests/test_formula_rules.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'pdf_trans.formula_rules'`.

- [ ] **Step 3: Implement named suspicion matches**

Create:

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SuspicionMatch:
    rule: str
    message: str
    matched_text: str


@dataclass(frozen=True)
class NormalizationResult:
    normalized_formula: str | None
    normalization_rule: str | None
    confidence: float | None


class FormulaNormalizer(Protocol):
    def normalize(
        self,
        raw_formula: str,
    ) -> NormalizationResult:
        raise NotImplementedError


class NoOpFormulaNormalizer:
    def normalize(
        self,
        raw_formula: str,
    ) -> NormalizationResult:
        return NormalizationResult(
            normalized_formula=None,
            normalization_rule=None,
            confidence=None,
        )
```

Use exact-boundary regular expressions:

```python
_NEQQ_RE = re.compile(r"\\neqq(?![A-Za-z])")
_COMPLEMENT_RE = re.compile(r"\\complement(?![A-Za-z])")
_METER_MAX_RE = re.compile(
    r"Meter\s+Ma(?:\s*\^\s*\{\s*)?\\max(?![A-Za-z])(?:\s*\})?"
)
_INSTRUMENT_RE = re.compile(
    r"(?<![A-Za-z])(?:F|H)\s+I\s+C(?![A-Za-z])"
)
_FRACTION_RE = re.compile(
    r"\\frac\s*\{\s*m\s+3\s*\}\s*\{\s*h\s*\}"
)
```

Implement a brace-depth scanner for each `\text{` occurrence and emit
`max_inside_text` when the balanced group's body contains exact command
`\\max(?![A-Za-z])`. Deduplicate rule matches only when rule ID, start offset,
and matched text are identical; retain distinct `F I C` and `H I C` matches.

- [ ] **Step 4: Run rule tests**

Run:

```bash
python -m pytest tests/test_formula_rules.py -q
```

Expected: all rule and no-op normalizer tests pass.

- [ ] **Step 5: Commit rules and normalizer interface**

```bash
git add src/pdf_trans/formula_rules.py tests/test_formula_rules.py
git commit -m "feat: classify suspicious MinerU formulas"
```

---

### Task 3: One-Process KaTeX Batch Validator and Runtime Packaging

**Files:**
- Create: `src/pdf_trans/formula_validation.py`
- Create: `src/pdf_trans/katex_validator.js`
- Create: `tests/test_formula_validation.py`
- Modify: `pyproject.toml:33-45`
- Modify: `Dockerfile:1-20`
- Modify: `tests/test_deployment_config.py`

**Interfaces:**
- Consumes: `tuple[ValidationInput, ...]`.
- Produces: `FormulaValidator.validate_batch(...) -> tuple[ValidationResult, ...]`.
- Produces: `KaTeXFormulaValidator` using exactly one Node process for a non-empty tuple.

- [ ] **Step 1: Write failing protocol, real-KaTeX, parity, and subprocess-count tests**

The tests define the intended DTOs and run the vendored KaTeX:

```python
from pathlib import Path

from pdf_trans.formula_validation import (
    KATEX_CONFIG,
    KATEX_VERSION,
    KaTeXFormulaValidator,
    ValidationInput,
)


def test_validates_complete_batch_with_vendored_katex():
    results = KaTeXFormulaValidator().validate_batch(
        (
            ValidationInput(
                "temperature",
                "{15}^{\\circ}\\mathrm{C}",
                False,
            ),
            ValidationInput("c7", "C_7 \\neqq C_6", True),
            ValidationInput("neq", "C_7 \\neq C_6", False),
        )
    )

    assert [value.formula_id for value in results] == [
        "temperature",
        "c7",
        "neq",
    ]
    assert [value.syntax_status for value in results] == [
        "valid",
        "invalid_syntax",
        "valid",
    ]
    assert results[1].validation_error.startswith("KaTeX parse error:")


def test_configuration_matches_frontend_source():
    root = Path(__file__).resolve().parents[1]
    reader = (
        root / "src/pdf_trans/web/static/reader.js"
    ).read_text(encoding="utf-8")
    source = (
        root / "src/pdf_trans/web/static/vendor/katex/SOURCE.txt"
    ).read_text(encoding="utf-8")

    assert KATEX_VERSION == "0.18.1"
    assert "KaTeX 0.18.1" in source
    assert KATEX_CONFIG == {
        "throwOnError": False,
        "trust": False,
        "maxSize": 20,
        "maxExpand": 500,
    }
    for fragment in (
        "throwOnError: false",
        "trust: false",
        "maxSize: 20",
        "maxExpand: 500",
        "{ left: '$$', right: '$$', display: true }",
        "{ left: '$', right: '$', display: false }",
        "{ left: '\\\\\\\\(', right: '\\\\\\\\)', display: false }",
        "{ left: '\\\\\\\\[', right: '\\\\\\\\]', display: true }",
    ):
        assert fragment in reader
```

Monkeypatch `subprocess.run` with a responder that returns a valid batch JSON,
call `validate_batch` with three inputs, and assert it was called once. Assert
empty input returns `()` without calling subprocess. Add tests that reject:
wrong KaTeX version/config, non-zero exit, non-JSON output, missing/duplicate/
reordered result IDs, and non-boolean display values.

Extend `tests/test_deployment_config.py`:

```python
def test_web_image_installs_node_for_formula_audit():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "apt-get install -y --no-install-recommends nodejs" in dockerfile
```

- [ ] **Step 2: Run validator and deployment tests and confirm RED**

Run:

```bash
python -m pytest \
  tests/test_formula_validation.py \
  tests/test_deployment_config.py -q
```

Expected: formula-validation collection fails because its module is absent,
and the Docker assertion fails because Node is not installed.

- [ ] **Step 3: Implement the Python validator DTOs and protocol**

Create:

```python
@dataclass(frozen=True)
class ValidationInput:
    formula_id: str
    formula: str
    is_block: bool


@dataclass(frozen=True)
class ValidationResult:
    formula_id: str
    syntax_status: Literal["valid", "invalid_syntax"]
    validation_error: str | None


class FormulaValidator(Protocol):
    def validate_batch(
        self,
        formulas: tuple[ValidationInput, ...],
    ) -> tuple[ValidationResult, ...]:
        raise NotImplementedError


KATEX_VERSION = "0.18.1"
KATEX_CONFIG = {
    "throwOnError": False,
    "trust": False,
    "maxSize": 20,
    "maxExpand": 500,
}
```

`KaTeXFormulaValidator` resolves the default script with
`Path(__file__).with_name("katex_validator.js")`, calls:

```python
subprocess.run(
    [self.node_binary, str(self.script_path)],
    input=json.dumps(payload, ensure_ascii=False),
    text=True,
    capture_output=True,
    check=False,
    timeout=self.timeout_seconds,
)
```

exactly once, then verifies returned version, config, result count, order, ID,
status, and error types. Convert `OSError`, `TimeoutExpired`, non-zero exit, and
protocol errors into `FormulaAuditError` without including formula source in
the message.

- [ ] **Step 4: Implement the Node stdin/stdout batch protocol**

`src/pdf_trans/katex_validator.js` must:

1. `require("./web/static/vendor/katex/katex.min.js")`;
2. parse one `{version, config, formulas}` JSON object from fd 0;
3. reject any requested version/config different from constants;
4. call `katex.renderToString` once for each formula with the shared config and
   `displayMode`;
5. classify markup containing `class="katex-error"` as invalid;
6. decode the error span's HTML-escaped `title` for `validation_error`;
7. output one ordered `{version, config, results}` JSON object;
8. write infrastructure errors to stderr and exit non-zero.

The validation loop's result shape is:

```javascript
{
  formula_id: value.formula_id,
  syntax_status: invalid ? "invalid_syntax" : "valid",
  validation_error: invalid ? errorTitle(markup) : null
}
```

Do not expose this file to or import it from `reader.js`.

- [ ] **Step 5: Package the script and install Node in the Web image**

Add to `pyproject.toml` before the existing `pdf_trans.web` package-data block:

```toml
"pdf_trans" = [
    "katex_validator.js",
]
```

Update `Dockerfile` before `pip install`:

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
```

Keep the final image unprivileged and do not install npm or download KaTeX.

- [ ] **Step 6: Run validator and packaging tests**

Run:

```bash
python -m pytest \
  tests/test_formula_validation.py \
  tests/test_deployment_config.py -q
python -m build --wheel
python -c "import glob,zipfile; p=glob.glob('dist/*.whl')[-1]; z=zipfile.ZipFile(p); assert any(n.endswith('pdf_trans/katex_validator.js') for n in z.namelist())"
```

Expected: pytest passes, wheel build succeeds, and the archive assertion exits
zero.

- [ ] **Step 7: Commit validator and runtime changes**

```bash
git add \
  src/pdf_trans/formula_validation.py \
  src/pdf_trans/katex_validator.js \
  tests/test_formula_validation.py \
  tests/test_deployment_config.py \
  pyproject.toml \
  Dockerfile
git commit -m "feat: batch validate formulas with vendored KaTeX"
```

---

### Task 4: Audit Classification, Atomic Report, and Aggregate Logs

**Files:**
- Create: `src/pdf_trans/formula_audit.py`
- Create: `tests/test_formula_audit.py`

**Interfaces:**
- Consumes: scanner candidates, a `FormulaValidator`, and a `FormulaNormalizer`.
- Produces: `FormulaAuditStats`, `FormulaAuditReport`, `audit_content_list_file`, `read_formula_audit_file`, and `log_formula_audit_summary`.

- [ ] **Step 1: Write failing end-to-end audit tests with a deterministic validator**

Use a test validator that records its batches and marks `\\neqq` and
`\\complement` invalid. Cover the discovered cases in one source file:

```python
class FakeValidator:
    def __init__(self):
        self.calls = []

    def validate_batch(self, formulas):
        self.calls.append(formulas)
        return tuple(
            ValidationResult(
                value.formula_id,
                (
                    "invalid_syntax"
                    if "\\neqq" in value.formula
                    or "\\complement" in value.formula
                    else "valid"
                ),
                (
                    "fake parse error"
                    if "\\neqq" in value.formula
                    or "\\complement" in value.formula
                    else None
                ),
            )
            for value in formulas
        )
```

The source fixture must include:

- `C_7 \neqq`;
- `C_4 \complement`;
- `\text{Meter Ma \max}`;
- `${15}^{\circ}\mathrm{C}$`;
- a table cell with `$F I C$`, `$H I C$`, and `$\frac{m 3}{h}$`;
- a genuine `C_7 \neq C_6`.

Assert:

```python
before = source.read_bytes()
report = audit_content_list_file(
    source,
    output,
    validator=validator,
)

assert source.read_bytes() == before
assert len(validator.calls) == 1
assert report.stats.total_formulas == 8
assert report.stats.invalid_syntax_count == 2
assert report.stats.suspicious_count == 6
assert output.exists()
payload = json.loads(output.read_text(encoding="utf-8"))
assert payload["validator"]["version"] == "0.18.1"
assert payload["summary"] == report.stats.to_dict()
assert payload["problem_page_indices"] == sorted(
    payload["problem_page_indices"]
)
assert all(
    value["syntax_status"] == "invalid_syntax"
    or value["suspicious"]
    for value in payload["issues"]
)
neq = next(
    value for value in payload["formulas"]
    if value["raw_formula"] == "$C_7 \\neq C_6$"
)
assert neq["statuses"] == ["valid"]
assert neq["normalized_formula"] is None
assert neq["normalization_rule"] is None
assert neq["confidence"] is None
```

Add tests for empty formula lists, invalid JSON/root, atomic replacement cleanup
on `os.replace` failure, report schema validation, a valid+suspicious status, an
invalid+suspicious status, sorted/deduplicated page arrays, and log messages
that do not contain raw formulas.

- [ ] **Step 2: Run audit tests and confirm RED**

Run:

```bash
python -m pytest tests/test_formula_audit.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'pdf_trans.formula_audit'`.

- [ ] **Step 3: Implement immutable stats and report models**

Use:

```python
@dataclass(frozen=True)
class FormulaAuditStats:
    total_formulas: int
    valid_count: int
    invalid_syntax_count: int
    suspicious_count: int
    issue_formula_count: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaAuditReport:
    stats: FormulaAuditStats
    problem_page_indices: tuple[int, ...]
    invalid_syntax_page_indices: tuple[int, ...]
    suspicious_page_indices: tuple[int, ...]
    payload: dict[str, object]
```

`read_formula_audit_file(path)` must validate:

- top-level object;
- `schema_version == 1`;
- exact validator metadata;
- all five non-negative integer summary fields;
- `valid_count + invalid_syntax_count == total_formulas`;
- list types for page arrays, `formulas`, and `issues`;
- unique/sorted integer page arrays;
- formula count equals `total_formulas`;
- issue count equals `issue_formula_count`.
- every formula has exactly the approved record keys, a unique stable
  `formula_id`, a 64-character lowercase hexadecimal `content_hash`, valid
  source/status values, and the required nullable table/normalization fields;
- every issue has a `formula_id` present in `formulas`, is actually invalid or
  suspicious, and the set of issue IDs exactly matches the invalid-or-
  suspicious records in `formulas`.

Return `FormulaAuditError` for invalid or unreadable reports.

- [ ] **Step 4: Implement record classification without normalization**

For each candidate and ordered validation result:

```python
matches = find_suspicious_formula(candidate.katex_formula)
normalization = normalizer.normalize(
    candidate.raw_formula
)
if any(
    value is not None
    for value in (
        normalization.normalized_formula,
        normalization.normalization_rule,
        normalization.confidence,
    )
):
    raise FormulaAuditError("第一版公式审计禁止自动规范化")
statuses = [validation.syntax_status]
if matches:
    statuses.append("suspicious")
```

Build records with every field in the approved design. Serialize
`suspicion_rules` as:

```python
[
    {
        "rule": match.rule,
        "message": match.message,
        "matched_text": match.matched_text,
    }
    for match in matches
]
```

`issues` must contain the same record objects selected by invalid syntax or
suspicion. Count suspicion per formula, not per matched rule.

- [ ] **Step 5: Implement read-only file orchestration and atomic output**

Implement the public signature
`audit_content_list_file(source: Path, output: Path, *, validator:
FormulaValidator | None = None, normalizer: FormulaNormalizer | None = None)
-> FormulaAuditReport`.

Read source bytes once, decode UTF-8, parse JSON, require a list, scan, batch
validate, classify, and construct this exact top-level payload:

```python
{
    "schema_version": 1,
    "validator": {
        "name": "katex",
        "version": KATEX_VERSION,
        "config": KATEX_CONFIG,
    },
    "summary": stats.to_dict(),
    "problem_page_indices": list(problem_pages),
    "invalid_syntax_page_indices": list(invalid_pages),
    "suspicious_page_indices": list(suspicious_pages),
    "formulas": records,
    "issues": issues,
}
```

For an empty candidate tuple, do not start Node; generate a zero-count report.
Write UTF-8 indented JSON with a trailing newline to
`.<output-name>.tmp`, flush and `os.fsync`, then `os.replace`. Remove the
temporary file on failure and raise `FormulaAuditError`. Never open `source`
for writing.

- [ ] **Step 6: Implement aggregate logging**

Implement:

```python
def log_formula_audit_summary(
    report: FormulaAuditReport,
    logger: logging.Logger = LOGGER,
) -> None:
    logger.info(
        "公式审计统计：总公式 %d，语法失败 %d，可疑公式 %d",
        report.stats.total_formulas,
        report.stats.invalid_syntax_count,
        report.stats.suspicious_count,
    )
    logger.info(
        "公式语法失败 page_idx：%s",
        list(report.invalid_syntax_page_indices),
    )
    logger.info(
        "可疑公式 page_idx：%s",
        list(report.suspicious_page_indices),
    )
```

Call it after a successful new audit and after reading a report for resume.

- [ ] **Step 7: Run audit, scanner, rules, and validator tests**

Run:

```bash
python -m pytest \
  tests/test_formula_scanner.py \
  tests/test_formula_rules.py \
  tests/test_formula_validation.py \
  tests/test_formula_audit.py -q
```

Expected: all formula-audit unit tests pass.

- [ ] **Step 8: Commit the report layer**

```bash
git add src/pdf_trans/formula_audit.py tests/test_formula_audit.py
git commit -m "feat: write read-only formula audit reports"
```

---

### Task 5: Full Workflow Integration Before Translation

**Files:**
- Modify: `src/pdf_trans/workflow.py:9-33,267-324`
- Modify: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `audit_content_list_file(source_path, formula_audit_path)`.
- Produces: the existing `WorkflowResult` unchanged; adds only the sibling `formula_audit.json` artifact.

- [ ] **Step 1: Write failing workflow ordering and non-blocking-finding tests**

Add a workflow test that monkeypatches the audit, cleaner, and translation
functions to append event names:

```python
def fake_audit(source, output):
    events.append(("audit", source, output))
    output.write_text('{"schema_version":1}', encoding="utf-8")
    return SimpleNamespace(
        stats=SimpleNamespace(
            total_formulas=1,
            invalid_syntax_count=1,
            suspicious_count=1,
        )
    )
```

Run `process_pdf` with a fake MinerU ZIP and translator, then assert:

```python
assert [value[0] for value in events].index("audit") < (
    [value[0] for value in events].index("clean")
)
assert [value[0] for value in events].index("audit") < (
    [value[0] for value in events].index("translate")
)
assert (
    tmp_path / "data/paper/hybrid_auto/formula_audit.json"
).exists()
```

Add a second test where `audit_content_list_file` raises
`FormulaAuditError("node unavailable")`; assert cleaner and translator are not
called and normalized/translated/Markdown outputs do not exist.

Add an integration test with a fake validator or monkeypatched audit report
showing invalid/suspicious formulas do not prevent the translator from
receiving the text object.

- [ ] **Step 2: Run the focused workflow tests and confirm RED**

Run:

```bash
python -m pytest \
  tests/test_workflow.py::test_process_pdf_audits_original_content_before_cleaning_and_translation \
  tests/test_workflow.py::test_process_pdf_stops_before_cleaning_when_formula_audit_infrastructure_fails \
  -q
```

Expected: tests fail because the workflow does not call the audit.

- [ ] **Step 3: Insert the logged audit stage after archive extraction**

Import `audit_content_list_file` in `workflow.py`. Immediately after the
extraction stage and before `output_path`/cleaning, add:

```python
formula_audit_path = source_path.parent / "formula_audit.json"
with logged_stage(
    LOGGER,
    "公式审计",
    "扫描 MinerU 原始公式并使用 KaTeX 批量校验",
) as stage:
    audit_report = audit_content_list_file(
        source_path,
        formula_audit_path,
    )
    stage.set_result(
        f"总公式 {audit_report.stats.total_formulas}，"
        f"语法失败 {audit_report.stats.invalid_syntax_count}，"
        f"可疑 {audit_report.stats.suspicious_count}，"
        f"报告 {formula_audit_path.resolve()}"
    )
```

Do not add formula fields to `WorkflowResult`, CLI summary output, normalized
content, translation calls, or Markdown calls.

- [ ] **Step 4: Run workflow and CLI regression tests**

Run:

```bash
python -m pytest tests/test_workflow.py tests/test_cli.py -q
```

Expected: all tests pass. Existing fixtures without formulas create a
zero-count audit without starting Node.

- [ ] **Step 5: Commit workflow integration**

```bash
git add src/pdf_trans/workflow.py tests/test_workflow.py
git commit -m "feat: audit MinerU formulas before translation"
```

---

### Task 6: Web Resume Backfill, Documentation, and Full Verification

**Files:**
- Modify: `src/pdf_trans/web/task_runner.py:3-84`
- Modify: `tests/web/test_task_runner.py`
- Modify: `README.md:225-315`

**Interfaces:**
- Consumes: `read_formula_audit_file(path)` and `audit_content_list_file(source, output)`.
- Produces: Web resume invariant that a valid `formula_audit.json` exists before `process_translation_file`.

- [ ] **Step 1: Write failing tests for reuse, backfill, and ambiguous raw input**

Extend the service fixture without breaking existing three-positional-argument
construction by adding two defaulted service fields. Test reuse:

```python
def test_runner_reuses_valid_formula_audit_before_resume(tmp_path):
    normalized = make_checkpoint(tmp_path)
    audit = normalized.with_name("formula_audit.json")
    audit.write_text('{"schema_version": 1}', encoding="utf-8")
    calls = []

    def read_report(path):
        calls.append(("read_audit", path))
        return fake_audit_report()

    def translate(path):
        calls.append(("translate", path))
        return fake_translation_result(path)

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(
            lambda *args, **kwargs: None,
            translate,
            fake_render,
            fake_audit,
            read_report,
        ),
    )
    runner.run(task_view(attempt_count=2))

    assert calls[:2] == [
        ("read_audit", audit),
        ("translate", normalized),
    ]
```

Test legacy backfill by creating `source_content_list.json` beside normalized
without `formula_audit.json`; assert `audit(source, audit_path)` precedes
translation. Test no raw candidate and two raw candidates; both must raise
`FormulaAuditError` and never translate. Retain the existing ambiguous
normalized-checkpoint test.

- [ ] **Step 2: Run focused Web resume tests and confirm RED**

Run:

```bash
python -m pytest \
  tests/web/test_task_runner.py::test_runner_reuses_valid_formula_audit_before_resume \
  tests/web/test_task_runner.py::test_runner_backfills_legacy_formula_audit_before_resume \
  -q
```

Expected: tests fail because resume currently translates immediately.

- [ ] **Step 3: Add injectable audit services and resume guard**

Import audit functions, `FormulaAuditReport`, and `FormulaAuditError`. Extend:

```python
@dataclass(frozen=True)
class WorkflowServices:
    process_pdf: Callable[..., WorkflowResult]
    process_translation_file: Callable[..., TranslationFileResult]
    render_content_list_file: Callable[[Path, Path], None]
    audit_content_list_file: Callable[
        [Path, Path], FormulaAuditReport
    ] = audit_content_list_file
    read_formula_audit_file: Callable[
        [Path], FormulaAuditReport
    ] = read_formula_audit_file
```

Add:

```python
_GENERATED_CONTENT_LIST_NAMES = {
    "cleaned_content_list.json",
    "normalized_content_list.json",
    "translated_content_list.json",
}


def _original_content_list(normalized: Path) -> Path:
    candidates = sorted(
        path
        for path in normalized.parent.glob("*_content_list.json")
        if path.name not in _GENERATED_CONTENT_LIST_NAMES
    )
    if len(candidates) != 1:
        raise FormulaAuditError(
            "断点续传前需要唯一的 MinerU 原始 content list，"
            f"实际找到 {len(candidates)} 个"
        )
    return candidates[0]
```

Implement a private `TaskRunner._ensure_formula_audit(normalized)`:

```python
audit_path = normalized.with_name("formula_audit.json")
if audit_path.is_file():
    report = self.services.read_formula_audit_file(audit_path)
else:
    source = _original_content_list(normalized)
    report = self.services.audit_content_list_file(source, audit_path)
log_formula_audit_summary(report)
```

Call it on the checkpoint branch immediately before
`process_translation_file`. Do not call it in `process_translation_file`, so
`--translate-only` remains unchanged.

- [ ] **Step 4: Run all Web runner tests**

Run:

```bash
python -m pytest tests/web/test_task_runner.py tests/web/test_worker.py -q
```

Expected: all tests pass; audit read/backfill always precedes translation.

- [ ] **Step 5: Document the new artifact and read-only behavior**

Update the output pipeline in `README.md` to:

```text
content_list.json
  -> formula_audit.json（只读公式审计报告）
  -> cleaned_content_list.json
  -> cross_page_candidates.json（诊断报告）
  -> normalized_content_list.json
  -> translated_content_list.json
  -> rendered.md
```

Add a short “公式审计” section stating:

- sources scanned;
- KaTeX 0.18.1 batch validation;
- `valid`, `invalid_syntax`, and independent `suspicious` classification;
- `formula_id`, location, hashes, normalization-reserved fields;
- findings do not modify or block content;
- infrastructure failures stop before translation;
- Web resume reuses or backfills the report.

- [ ] **Step 6: Run format-neutral diff checks and the full test suite**

Run:

```bash
git diff --check
python -m pytest -q
```

Expected: no whitespace errors and the complete suite passes.

- [ ] **Step 7: Exercise the real scanner-validator-report path**

Create no repository file. Use one shell heredoc only as a test driver:

```bash
python - <<'PY'
import json
import tempfile
from pathlib import Path
from pdf_trans.formula_audit import audit_content_list_file

with tempfile.TemporaryDirectory() as value:
    root = Path(value)
    source = root / "paper_content_list.json"
    output = root / "formula_audit.json"
    source.write_text(json.dumps([
        {
            "type": "text",
            "page_idx": 4,
            "bbox": [1, 2, 3, 4],
            "text": "ok ${15}^{\\circ}\\mathrm{C}$ bad $C_7 \\neqq C_6$",
        }
    ]), encoding="utf-8")
    before = source.read_bytes()
    report = audit_content_list_file(source, output)
    assert source.read_bytes() == before
    assert report.stats.total_formulas == 2
    assert report.stats.invalid_syntax_count == 1
    assert report.stats.suspicious_count == 1
    assert report.problem_page_indices == (4,)
    print(output)
PY
```

Expected: prints a temporary `formula_audit.json` path and exits zero.

- [ ] **Step 8: Build the Web image**

Run:

```bash
docker build -t pdf-trans-web:formula-audit .
docker run --rm --entrypoint node pdf-trans-web:formula-audit --version
```

Expected: image build succeeds and Node prints its version. Do not start the
Web server or contact MinerU/translation services in this verification.

- [ ] **Step 9: Commit Web resume and documentation**

```bash
git add \
  src/pdf_trans/web/task_runner.py \
  tests/web/test_task_runner.py \
  README.md
git commit -m "feat: enforce formula audit on Web resume"
```

---

## Final Verification Checklist

- [ ] `git status --short` shows only intentional plan-tracking changes, if any.
- [ ] `python -m pytest -q` passes with no failures.
- [ ] `python -m build --wheel` succeeds and the wheel contains both
  `pdf_trans/katex_validator.js` and the vendored KaTeX assets.
- [ ] The real smoke audit reports valid temperature, invalid/suspicious
  `\neqq`, and unchanged source bytes.
- [ ] The Docker image contains Node and runs as the existing unprivileged
  `appuser`.
- [ ] No diff exists in `src/pdf_trans/translation.py`,
  `src/pdf_trans/renderer.py`, `src/pdf_trans/web/static/reader.js`, or
  `src/pdf_trans/web/markdown.py`.
- [ ] `formula_audit.json` is produced beside the original content list before
  cleaned, normalized, translated, or Markdown artifacts.
