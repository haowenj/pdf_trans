# MinerU Formula Audit Design

## Objective

Add an independent, read-only formula audit between MinerU archive extraction
and the existing cleaning, normalization, and translation stages. The audit
discovers formulas in the original MinerU content list, validates them with the
same KaTeX version and rendering configuration as the Web reader, detects known
MinerU corruption patterns, and writes `formula_audit.json`.

The first version reports problems only. It must not mutate MinerU `text` or
`table_body`, normalize formulas, change translation behavior, change Markdown
generation, or change the frontend renderer.

## Scope

The primary integration target is the Web full-PDF workflow and its resume
operation.

- A new Web task runs the audit immediately after the MinerU archive is
  extracted and its original `*_content_list.json` is located.
- A resumed Web task reuses an existing valid audit report.
- A legacy resumed Web task that has a normalized checkpoint but no audit
  report locates the original MinerU content list beside the checkpoint and
  creates the missing report before translation resumes.
- `--translate-only` does not gain a formula-audit entry point in this change.
- Formula findings are non-blocking. Audit infrastructure failures, including
  an unreadable source file, an unavailable Node/KaTeX runtime, or an
  unwritable report, fail the workflow because no trustworthy audit was
  produced.

## Architecture

The implementation uses a Python audit module and one short-lived Node process
per audit.

Python owns:

- reading and validating the content-list JSON;
- formula discovery and source-location metadata;
- stable identifiers and hashes;
- table-grid positioning;
- suspicious-pattern detection;
- report aggregation and atomic JSON output;
- workflow and Web-resume integration.

Node owns only KaTeX batch validation. The Python validator passes every
formula in one JSON payload to one Node subprocess. The subprocess loads the
vendored frontend bundle at
`src/pdf_trans/web/static/vendor/katex/katex.min.js`; it does not install or
resolve another KaTeX package.

The data flow is:

```text
MinerU archive extraction
  -> locate original *_content_list.json
  -> read-only formula audit
  -> atomically write formula_audit.json
  -> existing cleaning and cross-page normalization
  -> existing translation
  -> existing Markdown rendering
```

No audit code receives a mutable output reference to the parsed content list.
Tests compare the original file bytes before and after auditing.

## Components and Interfaces

### Formula discovery

The scanner accepts the parsed top-level content-list array and emits immutable
formula candidates.

It scans:

1. `type=equation`: the complete `text` value is one display formula. Outer
   `$$...$$` or `\[...\]` delimiters are removed only from the KaTeX validation
   input. `raw_formula` retains the exact source value.
2. Every string `text` field: formulas are extracted using the frontend
   delimiter set, in source order:
   - `$$...$$`, display;
   - `$...$`, inline;
   - `\[...\]`, display;
   - `\(...\)`, inline.
3. Every `td` and `th` in `table_body`: HTML is parsed without rewriting it.
   Formula extraction runs on individual visible text nodes, matching the
   frontend auto-render boundary instead of incorrectly joining formulas across
   HTML elements. Multiple formulas in one cell are emitted separately.

Delimiter matching observes escaping. Unclosed delimiters are not treated as
complete formulas in this version.

### Table location

Table formulas include:

- `table_row_idx`;
- `table_col_idx`;
- `cell_tag`;
- `formula_index`.

Row and column indexes are zero-based. `table_col_idx` is the origin of the
cell in the expanded visual grid and accounts for preceding `rowspan` and
`colspan` occupancy. `formula_index` is zero-based within the containing source
field or table cell.

### Stable identity

Every formula has a full SHA-256 `content_hash` of its exact `raw_formula`.
`formula_id` is a deterministic digest with a `formula_` prefix. Its canonical
input contains:

- `page_idx`;
- `bbox`;
- `source_type`;
- `field_path`;
- table row and column, when present;
- `formula_index`;
- `content_hash`.

Canonical JSON serialization uses sorted keys and fixed separators. Repeating
the audit over an unchanged source produces identical IDs, while identical
formula text in different locations produces different IDs.

`field_path` is a JSON Pointer to the original field, such as `/42/text` or
`/57/table_body`. Table position and `formula_index` provide the more precise
location within `table_body`.

### FormulaValidator

Python exposes a `FormulaValidator` protocol with one batch operation. It
consumes all validation inputs and returns one ordered validation result per
input.

The production `KaTeXFormulaValidator`:

- starts Node once for the complete batch;
- loads vendored KaTeX 0.18.1;
- verifies the loaded `katex.version` is `0.18.1`;
- validates with the reader configuration:
  - `throwOnError: false`;
  - `trust: false`;
  - `maxSize: 20`;
  - `maxExpand: 500`;
- sets `displayMode` from each candidate's `is_block`;
- classifies KaTeX error output as `invalid_syntax`;
- preserves the KaTeX diagnostic in `validation_error`;
- rejects missing, duplicate, reordered, or malformed batch results.

Tests compare the validator constants and delimiter set with
`web/static/reader.js` so frontend drift is detected without modifying the
frontend renderer.

### FormulaNormalizer

Python exposes a `FormulaNormalizer` protocol and a no-op first-version
implementation. Audit records reserve:

- `raw_formula`;
- `normalized_formula`;
- `normalization_rule`;
- `confidence`.

The no-op implementation always leaves the latter three fields `null`.
No normalized value is passed to KaTeX, translation, Markdown generation, or
the content list.

## Classification

Syntax and suspicion are independent dimensions:

- `syntax_status` is `valid` or `invalid_syntax`;
- `suspicious` is a boolean;
- `statuses` contains either `valid` or `invalid_syntax`, plus `suspicious`
  when applicable.

Valid combinations are:

- `["valid"]`;
- `["valid", "suspicious"]`;
- `["invalid_syntax"]`;
- `["invalid_syntax", "suspicious"]`.

This preserves the distinction between syntax KaTeX can render and content that
is semantically likely to have been corrupted by MinerU.

## Suspicious Formula Rules

Each match produces a named entry in `suspicion_rules` with a human-readable
message and the matched source fragment. Rules annotate only and never replace
text.

The first version includes:

- `mineru_neqq`: exact `\neqq` control sequence;
- `mineru_complement`: exact `\complement` control sequence;
- `max_inside_text`: `\max` inside a balanced `\text{...}` group;
- `split_meter_max`: `Meter Ma \max`, `Meter Ma ^{\max}`, and whitespace
  variations;
- `split_instrument_tag`: spaced `F I C` and `H I C` variations;
- `split_cubic_meter_fraction`: `\frac{m 3}{h}` and whitespace variations
  inside the groups.

The `mineru_neqq` rule does not match the valid `\neq` command.

## Audit Record

Each formula record contains:

```json
{
  "formula_id": "formula_<deterministic digest>",
  "page_idx": 10,
  "bbox": [171, 126, 423, 145],
  "source_type": "equation",
  "field_path": "/42/text",
  "table_row_idx": null,
  "table_col_idx": null,
  "cell_tag": null,
  "formula_index": 0,
  "raw_formula": "$$...$$",
  "is_block": true,
  "content_hash": "<sha256>",
  "syntax_status": "valid",
  "suspicious": false,
  "statuses": ["valid"],
  "suspicion_rules": [],
  "validation_error": null,
  "normalized_formula": null,
  "normalization_rule": null,
  "confidence": null
}
```

The source object's `page_idx` and `bbox` are copied without coercing their
values. Missing values are represented as `null`.

## Report Format

`formula_audit.json` is UTF-8 JSON and contains:

```json
{
  "schema_version": 1,
  "validator": {
    "name": "katex",
    "version": "0.18.1",
    "config": {
      "throwOnError": false,
      "trust": false,
      "maxSize": 20,
      "maxExpand": 500
    }
  },
  "summary": {
    "total_formulas": 0,
    "valid_count": 0,
    "invalid_syntax_count": 0,
    "suspicious_count": 0,
    "issue_formula_count": 0
  },
  "problem_page_indices": [],
  "invalid_syntax_page_indices": [],
  "suspicious_page_indices": [],
  "formulas": [],
  "issues": []
}
```

`formulas` contains every record. `issues` contains the complete records whose
syntax is invalid or whose suspicious flag is true. Page arrays are unique and
sorted; `null` is not included in page arrays.

The writer creates a sibling temporary file and atomically replaces
`formula_audit.json`, so retries cannot observe a partially written report.

## Logging

Each completed audit logs:

- total formula count;
- invalid-syntax count and sorted `page_idx` values;
- suspicious count and sorted `page_idx` values.

Logs do not print full formulas. The existing Web task log handler carries
these messages into the page console and task log.

## Resume Behavior

A normalized checkpoint created by the new workflow implies that the audit
stage already completed because auditing precedes cleaning and normalization.

For resumed Web tasks:

1. If the sibling `formula_audit.json` is present and structurally valid, log
   its summary and continue the existing translation checkpoint.
2. If it is absent, locate exactly one original MinerU content list in the
   checkpoint directory by excluding generated names such as
   `cleaned_content_list.json`, `normalized_content_list.json`, and
   `translated_content_list.json`.
3. Audit that original file and write the missing report before calling the
   existing translation-resume function.
4. If no unique original file can be located, fail with an explicit audit
   error instead of silently resuming without a report.

This adds orchestration around resume but does not change
`translate_content_list_file`, translation checkpoint validation, Markdown
generation, or frontend rendering.

## Error Handling

- A non-array content-list root is an audit error.
- Non-object array members are ignored because they cannot contain specified
  formula fields.
- A non-string `text` or `table_body` is ignored for discovery.
- Malformed table HTML produces an audit error with the source field path; the
  source HTML remains untouched.
- Node startup, protocol, version, or KaTeX failures produce an audit error.
- Individual KaTeX parse failures are formula findings and do not fail the
  workflow.
- Suspicious findings are formula findings and do not fail the workflow.
- Report write failures fail the audit before translation starts.

## Testing

Tests use test-first development and cover:

- a C7 corruption expressed as `\neqq`;
- a C4 corruption expressed as `\complement`;
- MeterMax corruption expressed with `\max`;
- a valid `${15}^{\circ}\mathrm{C}$` formula;
- several formulas in one table cell;
- correct table row and expanded column positions;
- a genuine valid `\neq`, which is neither rewritten nor matched by
  `mineru_neqq`;
- syntax-valid plus suspicious combined classification;
- invalid-syntax plus suspicious combined classification;
- deterministic IDs across repeated scans;
- distinct IDs for identical formulas at distinct locations;
- unchanged source bytes before and after audit;
- one Node process for a multi-formula batch;
- KaTeX version, configuration, delimiter, and display-mode parity with the
  frontend;
- report totals, issue filtering, and sorted problem pages;
- required aggregate log output;
- a full workflow audit before cleaning and translation;
- legacy Web resume backfilling a missing report;
- Web resume reusing an existing valid report;
- infrastructure failure preventing translation;
- formula findings allowing translation to continue.

Existing translation, table translation, Markdown, Web reader, workflow, and
resume tests remain regression coverage for the explicitly unchanged
subsystems.
