# Minimal Paper Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the package to `pdf_trans` and append a sample OpenAI-compatible translation stage that writes `translated_content_list.json` and reports its counts.

**Architecture:** Keep the existing PDF workflow and move it under the new package name. Add a pure content transformation module driven by a small `TextTranslator` protocol, plus a separate environment-configured HTTP client; inject fakes in unit and workflow tests so tests never call a real model.

**Tech Stack:** Python 3.11, standard-library JSON/dataclasses/protocols, `httpx`, `pytest`.

## Global Constraints

- The supported module command is `python -m pdf_trans`; `python -m mineru_cleaner` is removed.
- Only objects whose `type` is exactly `"text"` are eligible.
- Sample mode attempts only the first 3 eligible text objects.
- Original `text`, all unrelated fields, array order, and object count remain unchanged.
- Success adds `translated_text` and `translation_status: "success"`.
- Failure adds `translated_text: null`, `translation_status: "failed"`, and `translation_error`, then processing continues.
- Later text objects receive only `translation_status: "pending"`.
- Non-text objects are copied unchanged.
- Configuration comes only from `TRANSLATION_BASE_URL`, `TRANSLATION_API_KEY`, and `TRANSLATION_MODEL`.
- Do not add concurrency, a database, agents, retries, a retry queue, or a web API.
- Tests must mock model calls and must never access a real translation service.

---

## File Structure

- Rename `src/mineru_cleaner/` to `src/pdf_trans/`: retain existing responsibilities under the supported package name.
- Create `src/pdf_trans/translation.py`: pure sample selection, per-item state handling, JSON I/O, and translation statistics.
- Create `src/pdf_trans/translation_client.py`: prompt, environment loading, OpenAI-compatible HTTP request, and response parsing.
- Modify `src/pdf_trans/errors.py`: rename the package-level base error and add translation configuration/content/client errors.
- Modify `src/pdf_trans/workflow.py`: run translation after normalization and expose translation output/counts.
- Modify `src/pdf_trans/__main__.py`: expose the renamed command and print translation summary.
- Modify `tests/test_*.py`: use `pdf_trans` imports and inject fake translators into workflow tests.
- Create `tests/test_package_name.py`: prove the package rename is complete.
- Create `tests/test_translation.py`: specify pure transformation and file behavior.
- Create `tests/test_translation_client.py`: specify environment and HTTP compatibility with `httpx.MockTransport`.
- Modify `README.md` and `pyproject.toml`: document the new package/command and output stage.

---

### Task 1: Complete Package Rename

**Files:**
- Create: `tests/test_package_name.py`
- Move: `src/mineru_cleaner/*.py` to `src/pdf_trans/*.py`
- Modify: `src/pdf_trans/*.py`
- Modify: `tests/test_*.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Consumes: existing package modules and pytest configuration.
- Produces: importable `pdf_trans` package; no importable `mineru_cleaner` package; `python -m pdf_trans`.

- [ ] **Step 1: Write the failing package-name test**

```python
import importlib.util


def test_pdf_trans_is_the_only_supported_package_name():
    assert importlib.util.find_spec("pdf_trans") is not None
    assert importlib.util.find_spec("mineru_cleaner") is None
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
pytest tests/test_package_name.py -v
```

Expected: FAIL because `pdf_trans` does not exist and `mineru_cleaner` still does.

- [ ] **Step 3: Move the package and update active imports**

Move every Python file from `src/mineru_cleaner/` to `src/pdf_trans/`. In active
source and test files, replace imports and monkeypatch targets from
`mineru_cleaner` to `pdf_trans`.

In `src/pdf_trans/__main__.py`, change:

```python
parser = argparse.ArgumentParser(
    prog="python -m pdf_trans",
    description="使用 MinerU 解析 PDF、清洗并翻译 content list。",
)
```

In `src/pdf_trans/errors.py`, rename the base class while retaining all specific
error subclasses:

```python
class PDFTransError(Exception):
    """Base exception for expected application failures."""


class ContentListError(PDFTransError):
    """Raised when a content-list file cannot be processed."""
```

Update `src/pdf_trans/__main__.py` to catch `PDFTransError`. Change
`pyproject.toml` metadata to:

```toml
[project]
name = "pdf-trans"
description = "Parse a PDF with MinerU, normalize its content list, and translate a sample."
```

Change README command examples to `python -m pdf_trans`.

- [ ] **Step 4: Run rename tests and the existing suite**

Run:

```bash
pytest tests/test_package_name.py -v
pytest -v
```

Expected: both PASS, with no `mineru_cleaner` matches in active source, tests,
`README.md`, or `pyproject.toml`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md src tests
git commit -m "refactor: rename package to pdf_trans"
```

---

### Task 2: Sample Content Transformation

**Files:**
- Create: `tests/test_translation.py`
- Create: `src/pdf_trans/translation.py`
- Modify: `src/pdf_trans/errors.py`

**Interfaces:**
- Consumes: `TextTranslator.translate(text: str) -> str`.
- Produces:
  - `TranslationStats(attempted_count, success_count, failed_count, pending_count)`;
  - `translate_items(items, translator, sample_limit=3) -> tuple[list[dict[str, Any]], TranslationStats]`;
  - `translate_content_list_file(source, output, translator) -> TranslationStats`.

- [ ] **Step 1: Write failing transformation tests**

Create tests equivalent to:

```python
import json

import pytest

from pdf_trans.errors import TranslationContentError
from pdf_trans.translation import (
    TranslationStats,
    translate_content_list_file,
    translate_items,
)


class FakeTranslator:
    def __init__(self, results):
        self.results = iter(results)
        self.received = []

    def translate(self, text):
        self.received.append(text)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_translate_items_attempts_first_three_text_objects_and_preserves_data():
    items = [
        {"type": "text", "text": "Alpha [38]", "page_idx": 0},
        {"type": "image", "img_path": "images/a.png"},
        {"type": "text", "text": "Beta $x^2$", "custom": {"a": 1}},
        {"type": "text", "text": "Gamma C-1"},
        {"type": "text", "text": "Delta 20%"},
    ]
    translator = FakeTranslator(["甲 [38]", RuntimeError("服务不可用"), "丙 C-1"])

    translated, stats = translate_items(items, translator)

    assert translator.received == ["Alpha [38]", "Beta $x^2$", "Gamma C-1"]
    assert translated == [
        {
            "type": "text",
            "text": "Alpha [38]",
            "page_idx": 0,
            "translated_text": "甲 [38]",
            "translation_status": "success",
        },
        {"type": "image", "img_path": "images/a.png"},
        {
            "type": "text",
            "text": "Beta $x^2$",
            "custom": {"a": 1},
            "translated_text": None,
            "translation_status": "failed",
            "translation_error": "服务不可用",
        },
        {
            "type": "text",
            "text": "Gamma C-1",
            "translated_text": "丙 C-1",
            "translation_status": "success",
        },
        {
            "type": "text",
            "text": "Delta 20%",
            "translation_status": "pending",
        },
    ]
    assert items[0] == {"type": "text", "text": "Alpha [38]", "page_idx": 0}
    assert len(translated) == len(items)
    assert stats == TranslationStats(3, 2, 1, 1)


def test_translate_items_attempts_all_eligible_items_when_fewer_than_three():
    translator = FakeTranslator(["甲"])
    translated, stats = translate_items(
        [{"type": "table"}, {"type": "text", "text": "Alpha"}],
        translator,
    )
    assert translated[0] == {"type": "table"}
    assert stats == TranslationStats(1, 1, 0, 0)


def test_translate_content_list_file_writes_utf8_json(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "text", "text": "Alpha"}]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(source, output, FakeTranslator(["阿尔法"]))

    assert stats == TranslationStats(1, 1, 0, 0)
    assert json.loads(output.read_text(encoding="utf-8"))[0]["translated_text"] == "阿尔法"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "无法读取规范化内容"),
        ('{"type": "text"}', "JSON 顶层必须是对象数组"),
        ('[{"type": "text"}, 1]', "JSON 顶层必须是对象数组"),
    ],
)
def test_translate_content_list_file_rejects_invalid_input(
    tmp_path,
    content,
    message,
):
    source = tmp_path / "normalized_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(TranslationContentError, match=message):
        translate_content_list_file(
            source,
            tmp_path / "translated_content_list.json",
            FakeTranslator([]),
        )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_translation.py -v
```

Expected: collection FAIL because `pdf_trans.translation` does not exist.

- [ ] **Step 3: Implement the minimal transformation**

Add to `src/pdf_trans/errors.py`:

```python
class TranslationContentError(PDFTransError):
    """Raised when translation content cannot be read or written."""
```

Create `src/pdf_trans/translation.py`:

```python
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pdf_trans.errors import TranslationContentError

SAMPLE_LIMIT = 3


class TextTranslator(Protocol):
    def translate(self, text: str) -> str:
        ...


@dataclass(frozen=True)
class TranslationStats:
    attempted_count: int
    success_count: int
    failed_count: int
    pending_count: int


def translate_items(
    items: list[dict[str, Any]],
    translator: TextTranslator,
    *,
    sample_limit: int = SAMPLE_LIMIT,
) -> tuple[list[dict[str, Any]], TranslationStats]:
    translated_items = copy.deepcopy(items)
    attempted = success = failed = pending = 0

    for item in translated_items:
        if item.get("type") != "text":
            continue
        if attempted >= sample_limit:
            item["translation_status"] = "pending"
            pending += 1
            continue

        attempted += 1
        try:
            translated_text = translator.translate(item["text"])
        except Exception as exc:
            item["translated_text"] = None
            item["translation_status"] = "failed"
            item["translation_error"] = str(exc) or exc.__class__.__name__
        else:
            item["translated_text"] = translated_text
            item["translation_status"] = "success"
            success += 1
            continue
        failed += 1

    return translated_items, TranslationStats(
        attempted_count=attempted,
        success_count=success,
        failed_count=failed,
        pending_count=pending,
    )


def translate_content_list_file(
    source: Path,
    output: Path,
    translator: TextTranslator,
) -> TranslationStats:
    try:
        items = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationContentError(f"无法读取规范化内容：{exc}") from exc
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise TranslationContentError("规范化内容的 JSON 顶层必须是对象数组")

    translated_items, stats = translate_items(items, translator)
    try:
        output.write_text(
            json.dumps(translated_items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise TranslationContentError(f"无法写入翻译结果：{exc}") from exc
    return stats
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest tests/test_translation.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pdf_trans/errors.py src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: translate sample content items"
```

---

### Task 3: Environment-Configured OpenAI-Compatible Client

**Files:**
- Create: `tests/test_translation_client.py`
- Create: `src/pdf_trans/translation_client.py`
- Modify: `src/pdf_trans/errors.py`

**Interfaces:**
- Consumes: three process environment variables and OpenAI-compatible
  `POST {base_url}/chat/completions`.
- Produces:
  - `OpenAICompatibleTranslator.from_env()`;
  - `OpenAICompatibleTranslator.translate(text: str) -> str`;
  - context-manager cleanup for owned `httpx.Client`.

- [ ] **Step 1: Write failing client tests**

```python
import json

import httpx
import pytest

from pdf_trans.errors import TranslationClientError, TranslationConfigError
from pdf_trans.translation_client import (
    OpenAICompatibleTranslator,
    TRANSLATION_SYSTEM_PROMPT,
)


def test_from_env_reads_required_translation_configuration(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1/")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.base_url == "http://translate.example/v1"
    assert translator.model == "paper-model"
    translator.close()


@pytest.mark.parametrize(
    "missing_name",
    ["TRANSLATION_BASE_URL", "TRANSLATION_API_KEY", "TRANSLATION_MODEL"],
)
def test_from_env_rejects_missing_configuration(monkeypatch, missing_name):
    for name in (
        "TRANSLATION_BASE_URL",
        "TRANSLATION_API_KEY",
        "TRANSLATION_MODEL",
    ):
        monkeypatch.setenv(name, "configured")
    monkeypatch.delenv(missing_name)

    with pytest.raises(TranslationConfigError, match=missing_name):
        OpenAICompatibleTranslator.from_env()


def test_translate_sends_compatible_request_and_returns_only_content():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "中文译文 [38]"}}]},
        )

    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1/",
        api_key="secret",
        model="paper-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = translator.translate("Source [38] with $x$ and C-1 at 20%.")

    assert result == "中文译文 [38]"
    assert seen["url"] == "http://translate.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer secret"
    assert seen["payload"] == {
        "model": "paper-model",
        "messages": [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Source [38] with $x$ and C-1 at 20%.",
            },
        ],
    }
    for required in ("[38]", "[39–41]", "LaTeX", "$...$", "C-1", "SS1", "只返回译文"):
        assert required in TRANSLATION_SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503), "翻译请求失败"),
        (httpx.Response(200, content=b"not-json"), "翻译请求失败"),
        (httpx.Response(200, json={"choices": []}), "翻译请求失败"),
        (
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  "}}]},
            ),
            "翻译响应缺少非空译文",
        ),
    ],
)
def test_translate_rejects_http_and_response_errors(response, message):
    translator = OpenAICompatibleTranslator(
        base_url="http://translate.example/v1",
        api_key="secret",
        model="paper-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: response)
        ),
    )

    with pytest.raises(TranslationClientError, match=message):
        translator.translate("Source")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest tests/test_translation_client.py -v
```

Expected: collection FAIL because `pdf_trans.translation_client` does not exist.

- [ ] **Step 3: Implement configuration and HTTP client**

Add to `src/pdf_trans/errors.py`:

```python
class TranslationConfigError(PDFTransError):
    """Raised when translation environment configuration is incomplete."""


class TranslationClientError(PDFTransError):
    """Raised when one translation model call fails."""
```

Create `src/pdf_trans/translation_client.py` with the exact required prompt and:

```python
class OpenAICompatibleTranslator:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=60.0)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleTranslator":
        values = {
            name: os.environ.get(name, "").strip()
            for name in (
                "TRANSLATION_BASE_URL",
                "TRANSLATION_API_KEY",
                "TRANSLATION_MODEL",
            )
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise TranslationConfigError(
                "缺少翻译环境变量：" + ", ".join(missing)
            )
        return cls(
            base_url=values["TRANSLATION_BASE_URL"],
            api_key=values["TRANSLATION_API_KEY"],
            model=values["TRANSLATION_MODEL"],
        )

    def __enter__(self) -> "OpenAICompatibleTranslator":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def translate(self, text: str) -> str:
        try:
            response = self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslationClientError(f"翻译请求失败：{exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise TranslationClientError("翻译响应缺少非空译文")
        return content
```

- [ ] **Step 4: Run client and transformation tests**

Run:

```bash
pytest tests/test_translation_client.py tests/test_translation.py -v
```

Expected: PASS, using only `httpx.MockTransport` and fakes.

- [ ] **Step 5: Commit**

```bash
git add src/pdf_trans/errors.py src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: add compatible translation client"
```

---

### Task 4: Workflow and CLI Integration

**Files:**
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_cli.py`
- Modify: `src/pdf_trans/workflow.py`
- Modify: `src/pdf_trans/__main__.py`

**Interfaces:**
- Consumes:
  - `translate_content_list_file(normalized_path, translated_path, translator)`;
  - injected `translator: TextTranslator | None`.
- Produces workflow result fields:
  - `translated_path`;
  - `translation_attempted_count`;
  - `translation_success_count`;
  - `translation_failed_count`;
  - `translation_pending_count`.

- [ ] **Step 1: Write failing workflow integration assertions**

Add a fake translator to `tests/test_workflow.py`:

```python
class FakeTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text):
        self.received.append(text)
        return f"译文：{text}"
```

Pass it as `translator=translator` to successful `process_pdf` tests and assert:

```python
assert translator.received == ["正文"]
assert result.translated_path == (
    tmp_path / "data/paper/hybrid_auto/translated_content_list.json"
).resolve()
assert result.translation_attempted_count == 1
assert result.translation_success_count == 1
assert result.translation_failed_count == 0
assert result.translation_pending_count == 0
assert json.loads(result.translated_path.read_text(encoding="utf-8")) == [
    {
        **body,
        "translated_text": "译文：正文",
        "translation_status": "success",
    },
    {"type": "chart", "img_path": "images/a.jpg"},
]
```

Track `write_normalized_content_list_file` and
`translate_content_list_file` calls to assert translation receives the exact
normalized path only after normalization has written it.

- [ ] **Step 2: Write failing CLI summary assertions**

Update fake `WorkflowResult` values in `tests/test_cli.py` with:

```python
translated_path=translated,
translation_attempted_count=3,
translation_success_count=2,
translation_failed_count=1,
translation_pending_count=4,
```

Then assert the output ends with:

```text
实际翻译对象数量：3
翻译成功数量：2
翻译失败数量：1
待翻译数量：4
翻译文件：<translated path>
```

- [ ] **Step 3: Run integration tests to verify RED**

Run:

```bash
pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: FAIL because `process_pdf` does not accept a translator and
`WorkflowResult` has no translation fields.

- [ ] **Step 4: Implement workflow integration**

In `src/pdf_trans/workflow.py`, add:

```python
from pdf_trans.translation import TextTranslator, translate_content_list_file
from pdf_trans.translation_client import OpenAICompatibleTranslator
```

Extend `WorkflowResult` with the five fields named in this task. Extend
`process_pdf`:

```python
def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
    translator: TextTranslator | None = None,
) -> WorkflowResult:
```

After writing the normalized file:

```python
translated_path = output_path.parent / "translated_content_list.json"
if translator is None:
    with OpenAICompatibleTranslator.from_env() as translation_client:
        translation_stats = translate_content_list_file(
            normalized_path,
            translated_path,
            translation_client,
        )
else:
    translation_stats = translate_content_list_file(
        normalized_path,
        translated_path,
        translator,
    )
```

Populate the resolved path and all counts in `WorkflowResult`.

In `src/pdf_trans/__main__.py`, print:

```python
print(f"实际翻译对象数量：{result.translation_attempted_count}")
print(f"翻译成功数量：{result.translation_success_count}")
print(f"翻译失败数量：{result.translation_failed_count}")
print(f"待翻译数量：{result.translation_pending_count}")
print(f"翻译文件：{result.translated_path}")
```

- [ ] **Step 5: Run integration tests to verify GREEN**

Run:

```bash
pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: PASS, with all workflow tests using injected fakes and no real model
traffic.

- [ ] **Step 6: Commit**

```bash
git add src/pdf_trans/workflow.py src/pdf_trans/__main__.py tests/test_workflow.py tests/test_cli.py
git commit -m "feat: integrate sample translation workflow"
```

---

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Verify: all active source and tests.

**Interfaces:**
- Consumes: completed workflow and CLI.
- Produces: documented environment setup, pipeline output, sample semantics,
  and verified package.

- [ ] **Step 1: Update README**

Document:

```bash
export TRANSLATION_BASE_URL="https://api.example.com/v1"
export TRANSLATION_API_KEY="replace-with-api-key"
export TRANSLATION_MODEL="paper-translation-model"
python -m pdf_trans /path/to/document.pdf
```

Extend the pipeline:

```text
normalized_content_list.json
  -> translated_content_list.json（sample：前 3 个 text）
```

Describe success, failure, pending, unchanged non-text behavior, and the five
translation summary lines.

- [ ] **Step 2: Run static repository checks**

Run:

```bash
rg -n "mineru_cleaner" src tests README.md pyproject.toml
git diff --check
```

Expected: the first command returns no matches; `git diff --check` is clean.

- [ ] **Step 3: Run complete automated verification**

Run:

```bash
pytest -v
python -m pdf_trans --help
```

Expected: all tests PASS; help output starts with usage for
`python -m pdf_trans`.

- [ ] **Step 4: Check environment presence without exposing secrets**

Run in a new interactive zsh:

```bash
zsh -ic 'for name in TRANSLATION_BASE_URL TRANSLATION_API_KEY TRANSLATION_MODEL; do if [[ -n ${(P)name} ]]; then print -- "$name=set"; else print -- "$name=missing"; fi; done'
```

Expected after the user's `.zshrc` update: three `=set` lines. This check does
not print values and does not call the service.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md
git commit -m "docs: document sample translation workflow"
```
