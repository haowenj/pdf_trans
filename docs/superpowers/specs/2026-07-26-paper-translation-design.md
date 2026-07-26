# Minimal Paper Translation Design

## Goal

Extend the existing PDF processing workflow with a minimal sample translation
stage. The stage reads the generated `normalized_content_list.json`, translates
at most the first three eligible text objects, and writes
`translated_content_list.json` in the same directory.

As part of this change, rename the Python package and module entry point from
`mineru_cleaner` to `pdf_trans`. The supported command becomes:

```bash
python -m pdf_trans /path/to/document.pdf
```

The old `python -m mineru_cleaner` entry point will not be retained.

## Architecture

The existing MinerU parsing, cleaning, cross-page normalization, and Markdown
rendering stages remain intact. A focused translation module will be added
under `src/pdf_trans/` and invoked by the workflow immediately after
`normalized_content_list.json` has been written.

The translation module will separate:

- OpenAI-compatible HTTP communication;
- content-list transformation and sample selection;
- JSON file reading and writing;
- result counts returned to the workflow and CLI.

The existing `httpx` dependency will call the OpenAI-compatible
`/chat/completions` endpoint directly. No OpenAI SDK or additional runtime
dependency is needed.

## Configuration

The client will read these variables from the process environment:

- `TRANSLATION_BASE_URL`
- `TRANSLATION_API_KEY`
- `TRANSLATION_MODEL`

All three values must be non-empty. Missing configuration is a workflow-level
configuration error: the command exits with an error and does not claim that
individual objects failed. Environment values are read at client creation
time, not at module import time.

The base URL is normalized by removing a trailing slash, then
`/chat/completions` is appended. This supports common values such as
`https://api.example.com/v1`.

## Translation Prompt

Each eligible object's original `text` value is sent without modification.
The system instruction requires the model to:

- accurately translate an English chemical-engineering academic paper into
  Simplified Chinese;
- not summarize, rewrite, or add information;
- preserve citation numbers such as `[38]` and `[39–41]`;
- preserve LaTeX formulas and `$...$` content;
- preserve numbers, units, and percentages;
- preserve equipment identifiers such as `C-1`, `E-1`, `D-1`, and `SS1`;
- use terminology appropriate for chemical-engineering papers;
- return only the translation, without explanations or prefixes.

The source text is supplied as a separate user message. A successful response
must contain a non-empty string at `choices[0].message.content`.

## Sample Selection and Data Preservation

The input must be a JSON array of objects. Objects are processed in their
original order and a new output array is built, so input objects are not
mutated.

For objects whose `type` is exactly `"text"`:

1. The first three such objects are attempted, regardless of whether an
   earlier attempt succeeds or fails.
2. On success, retain every original field and add:
   - `translated_text` containing the returned translation;
   - `translation_status: "success"`.
3. On failure, retain every original field and add:
   - `translated_text: null`;
   - `translation_status: "failed"`;
   - `translation_error` containing a concise error string.
4. Later text objects are not sent to the model. Retain every original field
   and add `translation_status: "pending"`. Do not add
   `translated_text` or `translation_error` to pending objects.

Objects whose `type` is not exactly `"text"` are copied unchanged. No
translation fields are added to them.

Output order and object count always equal the input order and object count.
If an input object already has translation-related fields, the stage overwrites
only the fields specified for its resulting state; unrelated fields remain
unchanged.

## Error Handling

Configuration errors and invalid top-level JSON structure are fatal and use
the project's expected application-error hierarchy.

Once translation starts, each selected object's model call is isolated.
Network errors, non-success HTTP responses, malformed response bodies, and
empty model output cause only that object to be marked failed. Processing then
continues with the next selected text object, and the output file is still
written.

File read and write failures remain workflow-level errors.

## Workflow Result and CLI Output

The workflow result will add:

- translated output path;
- attempted translation count;
- success count;
- failed count;
- pending count.

Attempted count is defined as success count plus failed count. It is at most
three and can be lower when the normalized content contains fewer than three
text objects.

After the existing output, the CLI prints:

```text
实际翻译对象数量：<attempted>
翻译成功数量：<success>
翻译失败数量：<failed>
待翻译数量：<pending>
翻译文件：<absolute path to translated_content_list.json>
```

## Testing

Tests will be written first. Model communication will always be mocked or
replaced by an injected fake translator; the test suite will never call a real
translation service.

Coverage will include:

- successful translation and preservation of all source fields;
- one selected call failing while later selected calls continue;
- exactly the first three text objects being attempted;
- later text objects being marked pending;
- non-text objects remaining byte-for-byte equivalent after JSON parsing;
- unchanged array length and order;
- fewer than three eligible text objects;
- environment configuration loading and missing-variable errors;
- request URL, headers, model, messages, and compatible response parsing using
  `httpx.MockTransport`;
- workflow placement after normalized JSON generation;
- translated file contents and result counts;
- renamed `pdf_trans` CLI and its summary output.

## Out of Scope

This change does not add concurrency, a database, agents, retries, a retry
queue, a web interface, batch controls, or a full-translation mode.
