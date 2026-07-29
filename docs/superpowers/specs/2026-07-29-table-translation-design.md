# Table Translation Design

## Goal

Add a first, minimal translation path for `type="table"` content-list objects
without changing the existing `type="text"` behavior.

The table HTML is stored in `table_body`. A successful translation preserves
the original `table_body` and stores the translated HTML separately. Rendering
uses the translated HTML only when the table translation succeeded. Any
table-specific failure keeps and renders the original HTML and does not stop
the rest of the document.

## Scope

This version:

- translates visible English text nodes inside `td`, `th`, and `caption`;
- skips nodes that are blank or contain no ASCII English letter, including
  pure-number cells;
- treats a mixed node such as `Density at 20 °C` as translatable as a whole;
- sends all eligible nodes from one table in one model call;
- assigns stable traversal-order IDs to nodes and fills translations by ID;
- preserves source tags, attributes, nesting, entities, comments, and
  non-translated text;
- validates the response IDs and the reconstructed HTML structure;
- logs and falls back to the original table HTML on any table-specific error.

This version does not add formula-aware splitting, footnote semantics, caption
fields outside the HTML, table-wide translation context, or cross-table
batching.

## Approaches Considered

### Standard-library `HTMLParser` with token-preserving reconstruction

Parse the HTML event stream, retain every original source token, and mark only
eligible data tokens for replacement. This adds no dependency and avoids a DOM
serializer rewriting attribute quoting, attribute order, or tag formatting.
It requires a small focused parser and explicit structural validation.

This is the selected approach.

### BeautifulSoup or lxml DOM parsing

DOM traversal and mutation are simpler, but neither library is currently a
project dependency. Their serializers can normalize otherwise valid source
HTML, which makes exact preservation of existing tags and attributes harder to
guarantee.

### Regular-expression replacement

This has no dependency cost but cannot reliably handle nested inline tags,
comments, entities, malformed markup, or matching start and end tags. It is
not suitable for the required failure guarantees.

## Architecture

Create a focused table-translation module responsible for:

1. parsing one `table_body`;
2. collecting eligible text-node records with IDs such as `table-text-0001`;
3. building one JSON batch request;
4. parsing a strict JSON response and mapping results by ID;
5. reconstructing HTML from preserved source tokens;
6. verifying that the reconstructed document has the same non-text structure.

The existing translation orchestrator remains responsible for retries,
concurrency, checkpoints, logging, and calling `TextTranslator.translate()`.
Its work collector dispatches both text paragraphs and tables, while retaining
the current text path unchanged.

## Data Flow

For a `type="table"` object with a non-empty `table_body`:

1. Parse the HTML and collect data tokens only while inside `td`, `th`, or
   `caption`. Ignore text under non-visible descendants such as `script`,
   `style`, and `template`.
2. Keep a node only when its stripped value contains at least one ASCII English
   letter. Pure numeric, punctuation-only, and whitespace-only nodes are never
   included in the model payload.
3. Assign IDs in source traversal order. The model request is a JSON object
   containing an instruction and an `items` array of `{id, text}` objects.
4. Call the existing `TextTranslator.translate()` once for the table.
5. Require a JSON response shaped as
   `{"translations": [{"id": "...", "text": "..."}]}`. Reject malformed JSON,
   duplicate IDs, extra IDs, missing IDs, blank translations, or a result count
   different from the requested node count.
6. Replace source data tokens by ID, preserving their original leading and
   trailing whitespace.
7. HTML-escape each translated text value before replacement, then reparse and
   compare structural signatures. The translated HTML must retain the same
   start tags, end tags, attributes, comments, declarations, and untranslated
   data tokens. Entities introduced by escaping translated text are treated as
   text content, not structure.
8. Store the result in `translated_table_body` and mark the table successful.

If a table has no eligible node, no model call is made. It is marked successful
with `translated_table_body` equal to the original `table_body`, so it cannot
remain pending or be retried indefinitely.

## State and Resume Behavior

Tables use the existing `translation_status`, `translation_error`, and
checkpoint conventions:

- `pending`: eligible table awaiting translation;
- `success`: `translated_table_body` contains validated translated HTML;
- `failed`: `translated_table_body` is absent, `table_body` remains original,
  and `translation_error` records a safe diagnostic.

Resume identity comparison includes the original `table_body`. Existing
successful tables are skipped; pending and failed tables are retried. Existing
`type="text"` state validation and output fields remain unchanged.

`TranslationStats.text_count` and paragraph success/failure counts retain their
existing text-only meaning so current CLI and web behavior is unchanged.
`model_call_count` includes table batch calls because it represents actual
model usage.

## Error Handling

All table-specific parse, response, mapping, reconstruction, and validation
errors are converted into a failed table outcome. The orchestrator logs the
table number and a concise error, writes a checkpoint, and continues processing
other work.

The logs do not include source HTML, prompt contents, or translated table
contents.

Process-level errors such as invalid source JSON or checkpoint write failures
retain their current behavior because they affect the whole translation file,
not one table.

## Rendering

For `type="table"`, rendering selects `translated_table_body` only when
`translation_status == "success"` and the value is non-blank. Otherwise it
renders the original `table_body`. Existing caption and footnote rendering
order remains unchanged.

## Tests

Unit tests cover:

- ordinary `td` and `th` English text;
- a pure-number cell excluded from the request and unchanged in output;
- unchanged `rowspan` and `colspan` attributes;
- several eligible nodes sent in one call and filled by ID despite a reordered
  response;
- caption text inside the HTML;
- a translator exception causing whole-table fallback while later document
  content continues;
- missing IDs, count mismatch, and invalid reconstructed HTML causing fallback;
- regression coverage proving the existing text request and result behavior is
  unchanged;
- renderer selection of successful translated table HTML and fallback HTML.
