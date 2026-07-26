# MinerU Content Cleaner Design

## Goal

Build a minimal Python command-line project that accepts one PDF path, submits
the PDF to the locally deployed MinerU 3.4.4 asynchronous task API, downloads
and extracts the returned ZIP archive under the project-local `data/`
directory, cleans the generated content-list JSON, and reports item counts.

The project does not generate Markdown, merge cross-page paragraphs or tables,
create a document IR, use agents or databases, or expose a web API.

## Command-Line Interface

The command accepts exactly one positional PDF path:

```bash
python -m mineru_cleaner /absolute/or/relative/path/document.pdf
```

The command validates that the path exists, is a regular file, and has a
case-insensitive `.pdf` suffix. MinerU is expected at
`http://127.0.0.1:7100`.

On success, the command prints:

- the number of items before cleaning;
- the number of filtered items;
- the number of items after cleaning; and
- the path of `cleaned_content_list.json`.

Errors are written as concise messages and cause a non-zero exit status.

## MinerU Request

The client submits the PDF with `POST /tasks`, using:

| Field | Value |
| --- | --- |
| `backend` | `hybrid-engine` |
| `parse_method` | `auto` |
| `effort` | `medium` |
| `formula_enable` | `true` |
| `table_enable` | `true` |
| `return_md` | `false` |
| `return_middle_json` | `false` |
| `return_model_output` | `false` |
| `return_content_list` | `true` |
| `return_images` | `true` |
| `response_format_zip` | `true` |

The server response must contain string values for `task_id`, `status_url`, and
`result_url`. The client polls `status_url` every two seconds:

- `pending` and `processing` continue polling;
- `completed` starts the ZIP download from `result_url`;
- `failed` stops and reports the server's error;
- an unknown status or malformed response stops with an error.

Polling has a 30-minute deadline. Individual HTTP requests also have finite
timeouts so that an unavailable server does not hang the command indefinitely.

## Output and ZIP Handling

The downloaded archive is temporary and is not committed. Its contents are
extracted directly below the project-local `data/` directory, preserving the
archive's directory structure. This yields paths shaped like:

```text
data/<pdf-stem>/<backend>_<parse-method>/...
```

ZIP entries with absolute paths, `..` components, or resolved targets outside
`data/` are rejected. A rerun for the same PDF replaces files at the same
archive-relative paths with the newest MinerU result; unrelated files in
`data/` are left untouched.

After extraction, the workflow recursively locates exactly one file whose name
ends with `_content_list.json`. A file ending in `_content_list_v2.json` does
not match. No match or multiple matches is an error. The cleaned result is
written alongside the matched source as:

```text
cleaned_content_list.json
```

The entire `data/` directory is ignored by Git.

## Cleaning Rules

The source JSON must be a top-level array. It is traversed once in its existing
array order. An item is removed only when one of these conditions holds:

1. `type` is `header`;
2. `type` is `footer`;
3. `type` is `page_number`; or
4. `type` is `text`, its `text` value is a string, and `text.strip()` is empty.

Every other item is retained. This explicitly includes:

- ordinary `text`;
- heading `text` with `text_level`;
- `image`;
- `table`;
- `chart`;
- `ref_text`; and
- unknown types introduced by MinerU in the future.

Retained JSON objects are written without changing, adding, or removing any of
their fields or values. Fields such as `page_idx`, `bbox`, `img_path`,
captions, footnotes, `content`, and `table_body` therefore remain intact.
Serialization may change insignificant JSON whitespace, but not the JSON data.

The reported counts satisfy:

```text
before_count = filtered_count + after_count
```

## Code Structure

The package uses focused modules:

- `mineru_cleaner.cleaner`: pure content-list filtering and JSON file writing;
- `mineru_cleaner.client`: MinerU task submission, polling, and ZIP download;
- `mineru_cleaner.archive`: safe extraction and content-list discovery;
- `mineru_cleaner.workflow`: orchestration from PDF to cleaned result;
- `mineru_cleaner.__main__`: argument parsing, reporting, and exit status.

`httpx` is the only runtime dependency. `pytest` is used for tests.

## Failure Handling

The workflow fails without producing a misleading success report when:

- the input is not a readable PDF path;
- MinerU is unavailable or returns a non-success HTTP response;
- a task fails, times out, or returns an invalid status payload;
- the result is not a ZIP response;
- the archive contains an unsafe path;
- the content-list file is missing or ambiguous;
- the JSON cannot be decoded or is not a top-level array; or
- an output file cannot be written.

## Test Strategy

Tests are written before production code and cover:

- all four removal rules;
- preservation of order, complete objects, headings, images, tables, charts,
  reference text, and unknown types;
- exact before, filtered, and after counts;
- MinerU task submission fields and PDF multipart upload;
- pending/processing/completed polling and failed-task handling;
- ZIP response download and safe extraction;
- rejection of path traversal entries;
- exact content-list discovery without selecting `_content_list_v2.json`;
- an end-to-end workflow using a synthetic MinerU ZIP; and
- command-line success reporting and non-zero failure behavior.

The test suite does not require a running MinerU server. After unit tests pass,
the implementation is also verified against the live local MinerU health
endpoint and, when practical, the provided sample PDF.
