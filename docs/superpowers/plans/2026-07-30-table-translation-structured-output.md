# Table Translation Structured Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make table translation requests use OpenAI-compatible JSON Schema structured output while leaving paragraph translation requests unchanged.

**Architecture:** Extend the existing `OpenAICompatibleTranslator.translate()` method with an optional `response_format` keyword and conditionally add it to the existing `httpx` JSON payload. Keep the table-specific schema next to the table request/response protocol in `table_translation.py`, and have only the table execution path pass it. Retain the existing local JSON and ID validation as a defense after the model response arrives.

**Tech Stack:** Python 3.11+, `httpx>=0.27,<1`, standard-library `json`, pytest 8

## Global Constraints

- Continue using the existing `httpx` `/chat/completions` request path.
- Do not add the OpenAI SDK or any other runtime dependency.
- Add `response_format` only to table translation requests.
- Keep ordinary paragraph translation responses as plain text.
- Use `response_format.type = "json_schema"` with `strict: true`.
- Keep the existing local table response parsing and validation.

## File Structure

- Modify `src/pdf_trans/translation_client.py`: conditionally serialize an explicitly supplied `response_format`.
- Modify `src/pdf_trans/table_translation.py`: build the table-specific JSON Schema from the prepared node IDs.
- Modify `src/pdf_trans/translation.py`: declare the optional structured-output interface and pass the schema only from the table path.
- Modify `tests/test_translation_client.py`: lock down HTTP request payload behavior with and without `response_format`.
- Modify `tests/test_table_translation.py`: verify the exact dynamic schema.
- Modify `tests/test_translation.py`: verify end-to-end table wiring and update translator test doubles to accept the optional keyword.

---

### Task 1: Optional `response_format` in the HTTP client

**Files:**
- Modify: `tests/test_translation_client.py:157-205`
- Modify: `src/pdf_trans/translation_client.py:151-170`

**Interfaces:**
- Consumes: existing `OpenAICompatibleTranslator.translate(text: str) -> str`
- Produces: `OpenAICompatibleTranslator.translate(text: str, *, response_format: dict[str, Any] | None = None) -> str`
- Guarantee: when `response_format` is `None`, the JSON request body still contains exactly `model` and `messages`

- [ ] **Step 1: Write the failing client request test**

Add this test after `test_translate_sends_compatible_request_and_returns_only_content` in `tests/test_translation_client.py`:

```python
def test_translate_includes_explicit_response_format():
    seen = {}
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "table_translation",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }

    def handler(request):
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"translations":[]}'}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="paper-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = translator.translate(
        "table request",
        response_format=response_format,
    )

    assert result == '{"translations":[]}'
    assert seen["payload"]["response_format"] == response_format
    assert set(seen["payload"]) == {"model", "messages", "response_format"}
```

Do not weaken the existing assertions at lines 181-193. They are the regression test proving ordinary paragraph calls omit `response_format`.

- [ ] **Step 2: Run the client test to verify RED**

Run:

```bash
pytest -q tests/test_translation_client.py::test_translate_includes_explicit_response_format
```

Expected: FAIL with `TypeError: OpenAICompatibleTranslator.translate() got an unexpected keyword argument 'response_format'`.

- [ ] **Step 3: Implement conditional request serialization**

Replace the beginning of `OpenAICompatibleTranslator.translate()` in `src/pdf_trans/translation_client.py` with:

```python
    def translate(
        self,
        text: str,
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            response = self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslationClientError(f"翻译请求失败：{exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise TranslationClientError("翻译响应缺少非空译文")
        return content
```

- [ ] **Step 4: Run client tests to verify GREEN**

Run:

```bash
pytest -q tests/test_translation_client.py
```

Expected: all tests PASS, including the existing exact-payload test without `response_format`.

- [ ] **Step 5: Commit the client increment**

```bash
git add src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: support structured translation responses"
```

---

### Task 2: Build the table translation JSON Schema

**Files:**
- Modify: `tests/test_table_translation.py:23-43`
- Modify: `src/pdf_trans/table_translation.py:45-85`

**Interfaces:**
- Consumes: `PreparedTableTranslation.nodes: tuple[TableTextNode, ...]`
- Produces: `PreparedTableTranslation.build_response_format() -> dict[str, object]`
- Guarantee: the schema permits only the current table node IDs and requires non-empty translated text

- [ ] **Step 1: Write the failing schema test**

Add this test after `test_extracts_caption_th_and_td_but_skips_numeric_and_hidden_text` in `tests/test_table_translation.py`:

```python
def test_builds_strict_response_format_for_current_node_ids():
    prepared = prepare_table_translation(
        "<table><tr><th>Component</th><td>Mass Fraction</td></tr></table>"
    )

    response_format = prepared.build_response_format()

    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "table_translation",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "translations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "enum": [
                                        "table-text-0001",
                                        "table-text-0002",
                                    ],
                                },
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                            },
                            "required": ["id", "text"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["translations"],
                "additionalProperties": False,
            },
        },
    }
```

- [ ] **Step 2: Run the schema test to verify RED**

Run:

```bash
pytest -q tests/test_table_translation.py::test_builds_strict_response_format_for_current_node_ids
```

Expected: FAIL with `AttributeError: 'PreparedTableTranslation' object has no attribute 'build_response_format'`.

- [ ] **Step 3: Implement the table schema builder**

Add this method to `PreparedTableTranslation` immediately after `build_request()` in `src/pdf_trans/table_translation.py`:

```python
    def build_response_format(self) -> dict[str, object]:
        if not self.nodes:
            raise TableTranslationError("表格没有待翻译节点")
        node_ids = [node.node_id for node in self.nodes]
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "table_translation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "translations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "enum": node_ids,
                                    },
                                    "text": {
                                        "type": "string",
                                        "minLength": 1,
                                    },
                                },
                                "required": ["id", "text"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["translations"],
                    "additionalProperties": False,
                },
            },
        }
```

- [ ] **Step 4: Extend the empty-table contract test**

In `test_table_with_no_eligible_text_builds_no_model_payload`, retain the existing `build_request()` assertion and add:

```python
    with pytest.raises(TableTranslationError, match="没有待翻译节点"):
        prepared.build_response_format()
```

- [ ] **Step 5: Run table protocol tests to verify GREEN**

Run:

```bash
pytest -q tests/test_table_translation.py
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the schema increment**

```bash
git add src/pdf_trans/table_translation.py tests/test_table_translation.py
git commit -m "feat: define table translation JSON schema"
```

---

### Task 3: Use structured output only for table translation

**Files:**
- Modify: `tests/test_translation.py:17-27`
- Modify: `tests/test_translation.py:416-481`
- Modify: `tests/test_translation.py:506-514`
- Modify: `src/pdf_trans/translation.py:23-25`
- Modify: `src/pdf_trans/translation.py:357-371`

**Interfaces:**
- Consumes: `PreparedTableTranslation.build_response_format() -> dict[str, object]`
- Consumes: `TextTranslator.translate(text: str, *, response_format: dict[str, Any] | None = None) -> str`
- Produces: table calls that always pass `response_format`; paragraph calls that continue to pass only `text`

- [ ] **Step 1: Update test doubles to expose the desired interface**

Change `FakeTranslator.translate()` in `tests/test_translation.py` to accept and record the optional keyword without changing existing `received` assertions:

```python
class FakeTranslator:
    def __init__(self, results):
        self.results = iter(results)
        self.received = []
        self.response_formats = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        self.response_formats.append(response_format)
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return result
```

Change `JsonTableTranslator` to:

```python
class JsonTableTranslator:
    def __init__(self, translations):
        self.translations = translations
        self.received = []
        self.response_formats = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        self.response_formats.append(response_format)
        payload = json.loads(text)
        return json.dumps(
            {
                "translations": [
                    {
                        "id": item["id"],
                        "text": self.translations[item["text"]],
                    }
                    for item in reversed(payload["items"])
                ]
            },
            ensure_ascii=False,
        )
```

Change `SelectiveTranslator.translate()` to:

```python
    def translate(self, text, *, response_format=None):
        self.received.append(text)
        if text == "Paragraph":
            assert response_format is None
            return "正文"
        assert response_format is not None
        raise RuntimeError("table service unavailable")
```

- [ ] **Step 2: Add the failing end-to-end table wiring assertion**

Add this import near the top of `tests/test_translation.py`:

```python
from pdf_trans.table_translation import prepare_table_translation
```

In `test_translate_file_batches_table_nodes_by_id_and_preserves_source_html`, after the existing request item assertion, add:

```python
    assert translator.response_formats == [
        prepare_table_translation(table_body).build_response_format()
    ]
```

- [ ] **Step 3: Run the wiring test to verify RED**

Run:

```bash
pytest -q tests/test_translation.py::test_translate_file_batches_table_nodes_by_id_and_preserves_source_html
```

Expected: FAIL because `translator.response_formats` contains `[None]` instead of the table JSON Schema.

- [ ] **Step 4: Extend the translator protocol and table call**

Change `TextTranslator` in `src/pdf_trans/translation.py` to:

```python
class TextTranslator(Protocol):
    def translate(
        self,
        text: str,
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        ...
```

Immediately after `request = prepared.build_request()` in `_translate_table_one`, build the schema once:

```python
    request = prepared.build_request()
    response_format = prepared.build_response_format()
```

Replace the table model call with:

```python
            response = translator.translate(
                request,
                response_format=response_format,
            )
```

Do not change `_translate_one`; its existing `translator.translate(text)` call is the required plain-text paragraph behavior.

- [ ] **Step 5: Run translation orchestration tests to verify GREEN**

Run:

```bash
pytest -q tests/test_translation.py
```

Expected: all tests PASS. In particular:

- `test_translate_file_batches_table_nodes_by_id_and_preserves_source_html` sees a JSON Schema.
- `test_table_translation_exception_falls_back_and_document_continues` proves paragraph calls receive `None` while table calls receive a schema.
- invalid table HTML and numeric-only tables still make no model request.

- [ ] **Step 6: Run focused feature regression tests**

Run:

```bash
pytest -q tests/test_translation_client.py tests/test_table_translation.py tests/test_translation.py tests/test_workflow.py
```

Expected: all tests PASS.

- [ ] **Step 7: Commit the integration increment**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: constrain table translation output"
```

---

### Task 4: Full verification

**Files:**
- Verify only; no planned file changes

**Interfaces:**
- Consumes: completed Tasks 1-3
- Produces: fresh evidence that the whole repository remains green and dependency declarations are unchanged

- [ ] **Step 1: Verify no OpenAI SDK dependency was added**

Run:

```bash
git diff e33fc16..HEAD -- pyproject.toml
```

Expected: no output.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
pytest -q
```

Expected: exit code 0 with zero failures.

- [ ] **Step 3: Check the final diff**

Run:

```bash
git status --short
git diff --check e33fc16..HEAD
git diff --stat e33fc16..HEAD
```

Expected:

- `git status --short` has no output.
- `git diff --check` has no output.
- The diff contains only the three source files and three test files listed in this plan.
