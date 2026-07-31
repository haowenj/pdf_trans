# MinerU Deterministic Formula Repair Design

## Objective

Add a deterministic, rule-only repair pass for the high-confidence MinerU
formula errors already represented in `formula_audit.json`. The repair runs
after MinerU parsing, preserves every original formula, validates each repair
candidate with the existing KaTeX validator, and ensures only accepted
`normalized_formula` values reach final Markdown rendering.

This change does not call a visual model or text model. It does not change
translation prompts, translation model selection, formula protection, or table
translation behavior.

## Scope

The first version repairs only these whitelist families:

1. MinerU `C` recognition errors involving `\complement`;
2. the paired alkene carbon-count expression containing `\neqq`;
3. fixed process-control fields;
4. numbered `FIC` and `HIC` instrument tags;
5. cubic metres per hour.

The implementation explicitly excludes general mathematical normalization,
global replacement of `\complement`, `\neq`, or `\neqq`, and uncertain errors
such as `\bar{\Pi}`, anomalous `\mathfrak`, or complex subscript/superscript
reconstruction.

The complete PDF workflow and Web resume workflow are in scope. The existing
standalone `--translate-only` entry point remains unchanged because it does not
have a guaranteed MinerU source and formula audit.

## Chosen Architecture

Use an audit-decision/render-consumption architecture:

```text
MinerU archive extraction
  -> immutable original *_content_list.json
  -> scan and validate raw formulas
  -> apply deterministic whitelist rules to repair candidates
  -> revalidate candidates with the existing KaTeX validator
  -> write formula_audit.json schema v2
  -> existing cleaning, cross-page normalization, and translation
  -> Markdown renderer applies accepted audit replacements
  -> rendered.md
```

The MinerU source and intermediate content-list files retain the raw formulas.
The audit is the authoritative repair decision. The renderer consumes only
accepted decisions, so a rejected candidate cannot alter the output.

This architecture also supports existing Web checkpoints. A legacy task can
regenerate its audit from the original MinerU content list and apply accepted
repairs while rendering without invalidating its normalized or translated
checkpoint.

### Alternatives considered

Generating a repaired content list before cleaning would make new workflows
straightforward, but it would make legacy normalized and translated
checkpoints disagree with their source identity. Repairing or discarding those
checkpoints would add unrelated translation-resume behavior and could repeat
model calls.

Overwriting the original or normalized content list was rejected because it
would destroy the direct raw-formula record and make rollback and audit
comparison less reliable.

## Rule Engine

The rule engine accepts one delimiter-free KaTeX formula and returns:

- the normalized KaTeX formula;
- an ordered tuple of rule identifiers;
- whether at least one rule changed the formula.

Rules run in a fixed order and operate on the output of preceding rules. A
single formula may therefore accumulate multiple rule identifiers. Rule
identifiers are included once, in first-application order. Applying the engine
to an already normalized formula must be idempotent.

Whitespace compatibility means `\s*` or `\s+` only at positions where MinerU
is known to insert spaces around LaTeX tokens, braces, or split characters. It
does not relax command boundaries or permit arbitrary intervening content.

### `mineru_complement_subscript_c`

Match `\complement` only when it is immediately followed, apart from
whitespace, by a braced decimal subscript:

```latex
\complement _ { 4 }
```

Normalize the matched atom to:

```latex
\mathrm{C}_{4}
```

The rest of the expression is preserved, so
`${ \complement _ { 4 } } ^ { = }$` becomes the delimiter-equivalent form of
`${ \mathrm{C}_{4} } ^ { = }$`.

The rule may match more than once, including:

```latex
\complement _ { 2 } . \complement _ { 7 }
```

An unqualified mathematical `\complement` is not changed.

### `mineru_degree_complement_c`

Match `\complement` only when immediately preceded, apart from whitespace, by
a braced degree superscript whose content is exactly `\circ`:

```latex
205 ^ { \circ } \complement
```

Normalize only the `\complement` atom:

```latex
205 ^ { \circ } \mathrm{C}
```

### `alkene_carbon_count_equality_pair`

Match the complete two-sided expression, not either comparison command in
isolation. Both sides must be a C atom with a braced decimal subscript, the
sides must be separated by `/`, and the left side must end in `\neqq`.

The known MinerU structure is:

```latex
\mathsf { C } _ { 7 } \neqq / \mathsf { C } _ { 8 } \neq
```

The right comparison may be `\neq` or `\neqq`, but neither command is changed
outside this complete paired structure. Normalize the complete expression to:

```latex
\mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}
```

The literal sequence `*{7}` is not part of this rule. It was only a Markdown
display artifact in the original request.

### `fixed_process_control_field`

Recognize only the complete fixed identifiers `MeterMax`, `SetPoint`,
`Output`, and `Equation`, either as their known split-letter forms or within a
balanced `\text { ... }` group.

For `MeterMax`, the accepted source forms include:

```latex
\text {M e t e r M a \max}
\text {M e t e r M a ^ {\max}}
\text {M e t e r M a ^ {m a x}}
M e t e r M a x
```

The three other identifiers use their exact split-letter sequences:

```latex
S e t P o i n t
O u t p u t
E q u a t i o n
```

Each complete identifier, including its `\text` wrapper when present, is
normalized to:

```latex
\mathrm{MeterMax}
\mathrm{SetPoint}
\mathrm{Output}
\mathrm{Equation}
```

Matching a `\text` group requires balanced braces. The rule does not perform a
general replacement of `\max` and does not normalize other spaced prose.

### `numbered_instrument_tag`

Match only `F I C` or `H I C` followed by a non-empty decimal digit sequence.
Whitespace may appear between the letters and between digits. Letter and digit
boundaries must prevent matching a substring of a longer alphanumeric token.

Examples:

```latex
F I C 0 0 1 2 -> \mathrm{FIC0012}
H I C 0 0 2 1 -> \mathrm{HIC0021}
```

Unnumbered text such as `F I C`, `Range of F I C`, and
`\mathrm { F I C }` remains unchanged.

### `cubic_metre_per_hour`

Match a `\frac` whose balanced numerator contains exactly `m`, whitespace, and
`3`, and whose balanced denominator contains exactly `h`, allowing known
spacing around the command and braces:

```latex
\frac { m 3 } { h }
```

Normalize to:

```latex
\frac{\mathrm{m}^{3}}{\mathrm{h}}
```

Already normalized units and other fractions are not changed.

## Delimiters and Structured Replacement

The scanner continues to retain exact `raw_formula`, including outer
delimiters, while exposing delimiter-free `katex_formula` for rules and
validation. Reconstructing `normalized_formula` preserves the original
delimiter style:

- a MinerU equation retains its original `$$...$$` or `\[...\]` wrapper and
  surrounding whitespace;
- inline formulas retain `$...$` or `\(...\)`;
- delimiter-free equation objects remain delimiter-free.

No accepted repair is applied through a global string replacement.

At render time, the renderer first chooses the field it would normally emit:

- successful text translation uses `translated_text`; otherwise `text`;
- successful table translation uses `translated_table_body`; otherwise
  `table_body`;
- equation objects use `text`.

It then scans formulas in that selected value using the existing delimiter and
HTML-visible-text-node rules. An accepted replacement is looked up by exact
`(raw_formula, is_block)` identity. Table replacement is limited to formula
spans inside visible `td` and `th` text nodes; attributes, hidden elements, and
non-formula text are untouched.

The audit reader rejects conflicting accepted replacements for the same
identity. Translation formula protection is expected to restore formulas
exactly; if a selected translated value does not contain the audited raw
formula, the renderer leaves it unchanged instead of attempting a fuzzy match.

## Validation

Raw validation remains the existing first KaTeX batch.

After every formula has run through all applicable rules, formulas with at
least one change are sent through the existing `KaTeXFormulaValidator` in a
second batch. The validator uses the existing vendored KaTeX 0.18.1 runtime,
configuration, display mode, and error-producing validation pass.

For each hit:

- valid normalized candidate: `normalization_status = "accepted"`;
- invalid normalized candidate: `normalization_status = "rejected"` and the
  KaTeX diagnostic is stored in `validation_error`.

A rejected formula continues through the document workflow and final rendering
with its raw formula. One rejected formula cannot fail a paragraph, table,
document, or batch.

An unavailable Node runtime, failed KaTeX subprocess, malformed batch
response, or other validator infrastructure error retains the current
workflow-failing behavior because no trustworthy audit can be produced.

## Formula Audit Schema v2

`formula_audit.json` changes to `schema_version: 2`.

Each formula record retains the existing identity, location, raw syntax, and
suspicion fields and replaces the first-version normalization placeholders
with:

```json
{
  "raw_formula": "$...$",
  "syntax_status": "valid",
  "raw_validation_error": null,
  "normalized_formula": "$...$",
  "normalization_rules": [
    "numbered_instrument_tag",
    "fixed_process_control_field",
    "cubic_metre_per_hour"
  ],
  "normalization_status": "accepted",
  "validation_error": null
}
```

Field semantics are:

- `raw_formula`: exact MinerU source, never overwritten;
- `raw_validation_error`: the original formula's KaTeX error, or `null`;
- `normalized_formula`: the complete reconstructed candidate, including its
  original delimiter style; `null` when no rule matched;
- `normalization_rules`: ordered rule identifiers; empty when no rule matched;
- `normalization_status`: `not_applicable`, `accepted`, or `rejected`;
- `validation_error`: normalized-candidate KaTeX error for rejected records,
  otherwise `null`.

The v1 `normalization_rule` singular field and `confidence` field are removed.
All rules are whitelist rules and do not expose a probabilistic confidence.

The summary retains existing syntax and suspicion counts and adds:

```json
{
  "scanned_formula_count": 100,
  "matched_formula_count": 8,
  "normalization_accepted_count": 7,
  "normalization_rejected_count": 1
}
```

`scanned_formula_count` equals the existing total-formula count. Both names
remain in v2 so existing audit consumers can keep using `total_formulas` while
the repair log uses the terminology requested for repair statistics.

The `issues` array continues to include raw invalid or suspicious formulas and
also includes every accepted or rejected normalization record. This ensures a
high-confidence repaired record is retained even if the original formula was
syntactically valid and was not covered by an older suspicion detector.

## Logging

Every completed repair audit logs one aggregate line containing:

- scanned formula count;
- matched formula count;
- accepted count;
- rejected count.

It then logs one detail entry per matched formula containing:

- `formula_id`;
- `page_idx`;
- raw formula;
- normalized candidate;
- ordered normalization rules;
- accepted or rejected status;
- validation error when rejected.

The report remains the machine-readable detailed log. Formula source is logged
because the requested repair detail explicitly requires before-and-after
formula text.

## Schema Migration and Resume

The v2 reader validates all record fields, counts, statuses, error semantics,
and accepted replacement consistency.

Web resume behavior is:

1. valid v2 report: reuse it;
2. valid legacy v1 report: locate the unique original MinerU content list and
   atomically regenerate `formula_audit.json` as v2;
3. missing report: generate v2 as today;
4. malformed v2 report: fail instead of silently replacing potentially
   corrupted state.

The renderer receives the v2 report on both the complete and resumed paths.
Legacy normalized and translated checkpoints are not overwritten. Accepted v2
repairs are applied only while creating the new `rendered.md`.

## Error Isolation

A rule implementation exception is an audit infrastructure error and fails the
audit before producing a partial report.

A KaTeX syntax failure for a normalized candidate is a per-formula rejection
and is non-blocking.

The renderer treats missing accepted source spans as a safe no-op. It never
falls back to substring, fuzzy, or global replacement.

Atomic report output remains mandatory. A regenerated v2 report cannot leave a
partially written `formula_audit.json`.

## Testing

Rule tests cover at least:

- `\complement_{4}^{=}`;
- `205^{\circ}\complement`;
- `\complement_{2} . \complement_{7}`;
- the actual raw alkene structure
  `\mathsf { C } _ {7}\neqq / \mathsf { C } _ {8}\neq`;
- three corrupted `MeterMax` forms and the split `MeterMax` form;
- `SetPoint`, `Output`, and `Equation`;
- numbered `F I C` and `H I C`;
- unnumbered `F I C` remaining unchanged;
- `\frac{m 3}{h}` and spacing variants;
- a genuine mathematical `\neq` remaining unchanged;
- unrelated mathematical `\complement` remaining unchanged;
- multiple rules and repeated occurrences in one formula;
- idempotence.

Audit tests cover:

- one raw-validation batch and one normalized-candidate validation batch;
- accepted and rejected record semantics;
- rejection retaining raw output behavior;
- schema v2 structural validation and summary consistency;
- before-and-after detail logging;
- v1 detection for migration;
- no mutation of the MinerU source file.

Renderer and workflow tests cover:

- accepted inline, equation, and table-cell formulas reaching Markdown;
- rejected formulas rendering raw content;
- translated and fallback field selection;
- no replacements in HTML attributes or non-formula text;
- a page-36-style control formula simultaneously repairing instrument tags,
  all fixed fields, and `m3/h`;
- a full PDF workflow passing the v2 audit to Markdown rendering;
- Web resume reusing v2;
- Web resume regenerating v1 and applying accepted formulas to Markdown
  without altering normalized or translated checkpoints.

The focused suite runs before the complete `pytest` suite. The production
KaTeX validator is exercised in integration tests so acceptance is not proven
only with a fake validator.

## Documentation

Update the README formula-audit and Markdown-rendering sections to state:

- schema v2 performs deterministic repairs;
- raw formulas remain preserved;
- accepted formulas are used only in final rendering;
- rejected candidates fall back to raw formulas;
- no model is used for formula repair;
- legacy v1 audits are regenerated on Web resume.
