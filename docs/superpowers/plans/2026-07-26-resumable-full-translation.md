# Resumable Full Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace sample translation with sequential full-text-object translation, reliable per-object atomic checkpoints, retry/timeout configuration, validated resume behavior, and a translation-only CLI entry.

**Architecture:** `translation.py` becomes the single file-level translation engine: it initializes or validates a checkpoint, processes every text object one at a time, retries failures, and atomically saves after each completed object. `translation_client.py` owns environment parsing and one-object HTTP calls; `workflow.py` exposes both the existing PDF flow and an explicit normalized-JSON translation flow, while the CLI selects between them.

**Tech Stack:** Python 3.11+, standard-library `copy`, `json`, `math`, `os`, `tempfile`, dataclasses and protocols; `httpx`; `pytest`.

## Global Constraints

- Input remains `normalized_content_list.json`.
- Output remains sibling `translated_content_list.json`.
- Every object whose `type` is exactly `"text"` is processed; all other objects remain unchanged and are not translated.
- Each model request contains exactly one text object's original `text`.
- A completed run leaves every text object in `success` or `failed`; no `pending` remains.
- One object failure is recorded and processing continues.
- A successful translation preserves original `text`, writes `translated_text`, sets `success`, and removes stale `translation_error`.
- Resume validates object count, order, `type` presence/value, and `text` presence/value before using an old result.
- Existing `success` is skipped; existing `pending` and `failed` are attempted.
- Every completed object is followed by an atomic full-file checkpoint.
- `TRANSLATION_TIMEOUT_SECONDS` defaults to `60` and must be a finite positive number.
- `TRANSLATION_MAX_RETRIES` defaults to `1` and counts retries after the first attempt.
- Model-call statistics include retries.
- Keep the existing PDF command and add `--translate-only normalized_content_list.json`.
- Do not add concurrency, batch requests, agents, long-text splitting, table translation, caption translation, a database, or a retry queue.
- All tests use fakes or `httpx.MockTransport`; no test calls a real service.

---

## File Structure

- Modify `src/pdf_trans/translation_client.py`: parse timeout/retry environment settings and expose them on the compatible translator.
- Modify `src/pdf_trans/translation.py`: remove sample limits, add resume validation, retries, full statistics, and atomic checkpoint writing.
- Modify `src/pdf_trans/workflow.py`: share translation execution between PDF and translation-only flows.
- Modify `src/pdf_trans/__main__.py`: support mutually exclusive PDF and `--translate-only` inputs and print formal statistics.
- Modify `tests/test_translation_client.py`: cover optional settings and invalid values.
- Modify `tests/test_translation.py`: replace sample tests with full translation, retry, resume, validation, interruption, and atomic-write tests.
- Modify `tests/test_workflow.py`: cover shared full/resume translation integration and translation-only path validation.
- Modify `tests/test_cli.py`: cover formal statistics and the translation-only command.
- Modify `README.md`: document formal behavior, settings, resume command, and final statistics.

---

### Task 1: Runtime Timeout and Retry Configuration

**Files:**
- Modify: `tests/test_translation_client.py`
- Modify: `src/pdf_trans/translation_client.py`

**Interfaces:**
- Consumes: `TRANSLATION_BASE_URL`, `TRANSLATION_API_KEY`, `TRANSLATION_MODEL`, optional `TRANSLATION_TIMEOUT_SECONDS`, and optional `TRANSLATION_MAX_RETRIES`.
- Produces:
  - `DEFAULT_TRANSLATION_TIMEOUT_SECONDS = 60.0`
  - `DEFAULT_TRANSLATION_MAX_RETRIES = 1`
  - `OpenAICompatibleTranslator.timeout_seconds: float`
  - `OpenAICompatibleTranslator.max_retries: int`

- [ ] **Step 1: Write failing environment-setting tests**

Add these tests to `tests/test_translation_client.py`:

```python
import math

import pdf_trans.translation_client as translation_client_module
from pdf_trans.translation_client import (
    DEFAULT_TRANSLATION_MAX_RETRIES,
    DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
)


def _set_required_environment(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")


def test_from_env_uses_default_timeout_and_retry_settings(monkeypatch):
    _set_required_environment(monkeypatch)
    monkeypatch.delenv("TRANSLATION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TRANSLATION_MAX_RETRIES", raising=False)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.timeout_seconds == DEFAULT_TRANSLATION_TIMEOUT_SECONDS
    assert translator.max_retries == DEFAULT_TRANSLATION_MAX_RETRIES
    translator.close()


def test_from_env_applies_configured_timeout_and_retries(monkeypatch):
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("TRANSLATION_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("TRANSLATION_MAX_RETRIES", "2")
    captured = {}

    class FakeHTTPClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(translation_client_module.httpx, "Client", FakeHTTPClient)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.timeout_seconds == 12.5
    assert translator.max_retries == 2
    assert captured["timeout"] == 12.5
    translator.close()
    assert captured["closed"] is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TRANSLATION_TIMEOUT_SECONDS", "0"),
        ("TRANSLATION_TIMEOUT_SECONDS", "-1"),
        ("TRANSLATION_TIMEOUT_SECONDS", "nan"),
        ("TRANSLATION_TIMEOUT_SECONDS", "inf"),
        ("TRANSLATION_TIMEOUT_SECONDS", "abc"),
        ("TRANSLATION_MAX_RETRIES", "-1"),
        ("TRANSLATION_MAX_RETRIES", "1.5"),
        ("TRANSLATION_MAX_RETRIES", "abc"),
    ],
)
def test_from_env_rejects_invalid_runtime_setting(monkeypatch, name, value):
    _set_required_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(TranslationConfigError, match=name):
        OpenAICompatibleTranslator.from_env()
```

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation_client.py -v
```

Expected: FAIL because the constants and translator attributes do not exist and optional environment settings are not parsed.

- [ ] **Step 3: Implement strict runtime configuration**

In `src/pdf_trans/translation_client.py`, add:

```python
import math

DEFAULT_TRANSLATION_TIMEOUT_SECONDS = 60.0
DEFAULT_TRANSLATION_MAX_RETRIES = 1


def _read_timeout_seconds() -> float:
    raw = os.environ.get(
        "TRANSLATION_TIMEOUT_SECONDS",
        str(DEFAULT_TRANSLATION_TIMEOUT_SECONDS),
    ).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise TranslationConfigError(
            "TRANSLATION_TIMEOUT_SECONDS 必须是有限正数"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise TranslationConfigError(
            "TRANSLATION_TIMEOUT_SECONDS 必须是有限正数"
        )
    return value


def _read_max_retries() -> int:
    raw = os.environ.get(
        "TRANSLATION_MAX_RETRIES",
        str(DEFAULT_TRANSLATION_MAX_RETRIES),
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise TranslationConfigError(
            "TRANSLATION_MAX_RETRIES 必须是非负整数"
        ) from exc
    if value < 0:
        raise TranslationConfigError(
            "TRANSLATION_MAX_RETRIES 必须是非负整数"
        )
    return value
```

Extend the constructor:

```python
def __init__(
    self,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float = DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_TRANSLATION_MAX_RETRIES,
    http_client: httpx.Client | None = None,
) -> None:
    self.base_url = base_url.rstrip("/")
    self.model = model
    self.timeout_seconds = timeout_seconds
    self.max_retries = max_retries
    self._api_key = api_key
    self._owns_client = http_client is None
    self._http = http_client or httpx.Client(timeout=timeout_seconds)
```

In `from_env`, parse optional settings before constructing the translator:

```python
return cls(
    base_url=values["TRANSLATION_BASE_URL"],
    api_key=values["TRANSLATION_API_KEY"],
    model=values["TRANSLATION_MODEL"],
    timeout_seconds=_read_timeout_seconds(),
    max_retries=_read_max_retries(),
)
```

- [ ] **Step 4: Run client tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation_client.py -v
```

Expected: all client tests PASS and use only fakes or `MockTransport`.

- [ ] **Step 5: Commit**

```bash
git add src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: configure translation timeout and retries"
```

---

### Task 2: Full Translation, Resume Validation, and Atomic Checkpoints

**Files:**
- Modify: `tests/test_translation.py`
- Modify: `src/pdf_trans/translation.py`

**Interfaces:**
- Consumes:
  - normalized object array;
  - optional existing translated object array;
  - `TextTranslator.translate(text: str) -> str`;
  - `max_retries: int`;
  - optional checkpoint writer callback.
- Produces:
  - `TranslationStats(text_count, model_call_count, skipped_success_count, success_count, failed_count, pending_count)`;
  - `write_json_atomic(output: Path, items: list[dict[str, Any]]) -> None`;
  - `translate_content_list_file(source, output, translator, *, max_retries=1, checkpoint_writer=None) -> TranslationStats`.

- [ ] **Step 1: Replace sample tests with failing full-translation and retry tests**

Keep the existing `FakeTranslator`, but make exhausted or exception results explicit. Add:

```python
import copy
import os


class FakeTranslator:
    def __init__(self, results):
        self.results = iter(results)
        self.received = []

    def translate(self, text):
        self.received.append(text)
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return result


def test_translates_every_text_sequentially_and_leaves_no_pending(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    items = [
        {"type": "text", "text": f"Text {index}", "index": index}
        for index in range(12)
    ]
    items.insert(4, {"type": "table", "table_body": "<table></table>"})
    source.write_text(json.dumps(items), encoding="utf-8")
    translator = FakeTranslator([f"译文 {index}" for index in range(12)])
    snapshots = []

    def recording_writer(path, current):
        snapshots.append(copy.deepcopy(current))
        path.write_text(json.dumps(current), encoding="utf-8")

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=0,
        checkpoint_writer=recording_writer,
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert translator.received == [f"Text {index}" for index in range(12)]
    assert result[4] == {"type": "table", "table_body": "<table></table>"}
    assert all(
        item["translation_status"] == "success"
        for item in result
        if item.get("type") == "text"
    )
    assert stats == TranslationStats(
        text_count=12,
        model_call_count=12,
        skipped_success_count=0,
        success_count=12,
        failed_count=0,
        pending_count=0,
    )
    assert len(snapshots) == 13


def test_retries_failure_then_continues_after_final_failure(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "A"},
                {"type": "text", "text": "B"},
                {"type": "text", "text": "C"},
            ]
        ),
        encoding="utf-8",
    )
    translator = FakeTranslator(
        [
            RuntimeError("A first"),
            "译文 A",
            RuntimeError("B first"),
            RuntimeError("B second"),
            "译文 C",
        ]
    )

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert translator.received == ["A", "A", "B", "B", "C"]
    assert result[0]["translation_status"] == "success"
    assert "translation_error" not in result[0]
    assert result[1]["translated_text"] is None
    assert result[1]["translation_status"] == "failed"
    assert result[1]["translation_error"] == "B second"
    assert result[2]["translation_status"] == "success"
    assert stats.model_call_count == 5
    assert stats.success_count == 2
    assert stats.failed_count == 1
    assert stats.pending_count == 0
```

- [ ] **Step 2: Add failing resume and interruption tests**

Add:

```python
def test_resume_skips_success_and_retries_pending_and_failed(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    normalized = [
        {"type": "text", "text": "A", "page_idx": 0},
        {"type": "image", "img_path": "a.png"},
        {"type": "text", "text": "B", "page_idx": 1},
        {"type": "text", "text": "C", "page_idx": 2},
    ]
    existing = [
        {
            **normalized[0],
            "translated_text": "已有 A",
            "translation_status": "success",
            "translation_error": "stale",
        },
        normalized[1],
        {**normalized[2], "translation_status": "pending"},
        {
            **normalized[3],
            "translated_text": None,
            "translation_status": "failed",
            "translation_error": "old failure",
        },
    ]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(json.dumps(existing), encoding="utf-8")
    translator = FakeTranslator(["新译 B", "新译 C"])

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=0,
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert translator.received == ["B", "C"]
    assert result[0]["translated_text"] == "已有 A"
    assert "translation_error" not in result[0]
    assert result[1] == normalized[1]
    assert result[2]["translated_text"] == "新译 B"
    assert result[3]["translated_text"] == "新译 C"
    assert stats.skipped_success_count == 1
    assert stats.model_call_count == 2
    assert stats.success_count == 3
    assert stats.pending_count == 0


def test_interruption_checkpoint_resumes_without_repeating_success(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "A"},
                {"type": "text", "text": "B"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(KeyboardInterrupt):
        translate_content_list_file(
            source,
            output,
            FakeTranslator(["译文 A", KeyboardInterrupt()]),
            max_retries=0,
        )

    checkpoint = json.loads(output.read_text(encoding="utf-8"))
    assert checkpoint[0]["translation_status"] == "success"
    assert checkpoint[1]["translation_status"] == "pending"

    translator = FakeTranslator(["译文 B"])
    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=0,
    )

    assert translator.received == ["B"]
    assert stats.skipped_success_count == 1
    assert stats.success_count == 2
```

- [ ] **Step 3: Add failing mismatch, invalid-state, and atomic-write tests**

Add:

```python
@pytest.mark.parametrize("mutation", ["count", "order", "type", "text"])
def test_resume_rejects_identity_mismatch_without_modifying_output(
    tmp_path,
    mutation,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    normalized = [
        {"type": "text", "text": "A"},
        {"type": "text", "text": "B"},
    ]
    existing = [
        {
            **item,
            "translated_text": f"译文 {item['text']}",
            "translation_status": "success",
        }
        for item in normalized
    ]
    if mutation == "count":
        existing.pop()
    elif mutation == "order":
        existing.reverse()
    elif mutation == "type":
        existing[0]["type"] = "image"
    else:
        existing[0]["text"] = "changed"
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(json.dumps(existing), encoding="utf-8")
    original_bytes = output.read_bytes()

    with pytest.raises(TranslationContentError, match="不一致"):
        translate_content_list_file(
            source,
            output,
            FakeTranslator([]),
        )

    assert output.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"translation_status": "unknown"},
        {"translation_status": "success", "translated_text": ""},
        {
            "translation_status": "failed",
            "translated_text": "not-null",
            "translation_error": "error",
        },
        {
            "translation_status": "failed",
            "translated_text": None,
            "translation_error": "",
        },
    ],
)
def test_resume_rejects_invalid_text_state(tmp_path, fields):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    normalized = [{"type": "text", "text": "A"}]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(json.dumps([{**normalized[0], **fields}]), encoding="utf-8")

    with pytest.raises(TranslationContentError, match="翻译状态"):
        translate_content_list_file(source, output, FakeTranslator([]))


def test_write_json_atomic_fsyncs_then_replaces(tmp_path, monkeypatch):
    output = tmp_path / "translated_content_list.json"
    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracking_fsync(file_descriptor):
        events.append("fsync")
        real_fsync(file_descriptor)

    def tracking_replace(source, target):
        events.append(("replace", Path(source).parent, Path(target)))
        assert json.loads(Path(source).read_text(encoding="utf-8")) == [
            {"type": "text", "text": "A"}
        ]
        real_replace(source, target)

    monkeypatch.setattr("pdf_trans.translation.os.fsync", tracking_fsync)
    monkeypatch.setattr("pdf_trans.translation.os.replace", tracking_replace)

    write_json_atomic(output, [{"type": "text", "text": "A"}])

    assert events[0] == "fsync"
    assert events[1] == ("replace", tmp_path, output)
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {"type": "text", "text": "A"}
    ]
```

- [ ] **Step 4: Run translation tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation.py -v
```

Expected: FAIL because sample-limit behavior, old statistics, no resume validation, and non-atomic writes do not satisfy the tests.

- [ ] **Step 5: Implement the complete file engine**

Replace `src/pdf_trans/translation.py` with focused helpers and the public API:

```python
from __future__ import annotations

import copy
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pdf_trans.errors import TranslationContentError


class TextTranslator(Protocol):
    def translate(self, text: str) -> str:
        ...


@dataclass(frozen=True)
class TranslationStats:
    text_count: int
    model_call_count: int
    skipped_success_count: int
    success_count: int
    failed_count: int
    pending_count: int


CheckpointWriter = Callable[[Path, list[dict[str, Any]]], None]


def _read_object_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationContentError(f"无法读取{label}：{exc}") from exc
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise TranslationContentError(f"{label}的 JSON 顶层必须是对象数组")
    return payload


def write_json_atomic(
    output: Path,
    items: list[dict[str, Any]],
) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(items, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    except (OSError, TypeError, ValueError) as exc:
        raise TranslationContentError(f"无法原子写入翻译结果：{exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _same_identity(
    normalized: dict[str, Any],
    existing: dict[str, Any],
    key: str,
) -> bool:
    return (
        key in normalized,
        normalized.get(key),
    ) == (
        key in existing,
        existing.get(key),
    )


def _prepare_new_items(
    normalized_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    working_items = copy.deepcopy(normalized_items)
    for item in working_items:
        if item.get("type") == "text":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
    return working_items


def _prepare_resumed_items(
    normalized_items: list[dict[str, Any]],
    existing_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if len(normalized_items) != len(existing_items):
        raise TranslationContentError("旧翻译结果与规范化内容的对象数量不一致")

    working_items = copy.deepcopy(normalized_items)
    skipped_success = 0
    for index, (normalized, existing, working) in enumerate(
        zip(normalized_items, existing_items, working_items)
    ):
        if not _same_identity(normalized, existing, "type") or not _same_identity(
            normalized, existing, "text"
        ):
            raise TranslationContentError(
                f"旧翻译结果第 {index} 个对象的顺序、type 或 text 不一致"
            )
        if normalized.get("type") != "text":
            continue

        status = existing.get("translation_status")
        if status == "success":
            translated_text = existing.get("translated_text")
            if not isinstance(translated_text, str) or not translated_text.strip():
                raise TranslationContentError(
                    f"旧翻译结果第 {index} 个 text 的翻译状态无效"
                )
            working["translated_text"] = translated_text
            working["translation_status"] = "success"
            working.pop("translation_error", None)
            skipped_success += 1
        elif status == "pending":
            working.pop("translated_text", None)
            working.pop("translation_error", None)
            working["translation_status"] = "pending"
        elif status == "failed":
            error = existing.get("translation_error")
            if existing.get("translated_text") is not None or not isinstance(
                error, str
            ) or not error.strip():
                raise TranslationContentError(
                    f"旧翻译结果第 {index} 个 text 的翻译状态无效"
                )
            working["translated_text"] = None
            working["translation_status"] = "failed"
            working["translation_error"] = error
        else:
            raise TranslationContentError(
                f"旧翻译结果第 {index} 个 text 的翻译状态无效"
            )
    return working_items, skipped_success


def _translate_one(
    item: dict[str, Any],
    translator: TextTranslator,
    max_retries: int,
) -> int:
    model_calls = 0
    for attempt in range(max_retries + 1):
        model_calls += 1
        try:
            translated_text = translator.translate(item["text"])
            if (
                not isinstance(translated_text, str)
                or not translated_text.strip()
            ):
                raise ValueError("翻译响应缺少非空译文")
        except Exception as exc:
            if attempt < max_retries:
                continue
            item["translated_text"] = None
            item["translation_status"] = "failed"
            item["translation_error"] = str(exc) or exc.__class__.__name__
        else:
            item["translated_text"] = translated_text
            item["translation_status"] = "success"
            item.pop("translation_error", None)
            break
    return model_calls


def translate_content_list_file(
    source: Path,
    output: Path,
    translator: TextTranslator,
    *,
    max_retries: int = 1,
    checkpoint_writer: CheckpointWriter | None = None,
) -> TranslationStats:
    if max_retries < 0:
        raise TranslationContentError("max_retries 必须是非负整数")
    normalized_items = _read_object_array(source, "规范化内容")
    if output.exists():
        if not output.is_file():
            raise TranslationContentError("翻译结果路径存在但不是普通文件")
        existing_items = _read_object_array(output, "旧翻译结果")
        working_items, skipped_success = _prepare_resumed_items(
            normalized_items,
            existing_items,
        )
    else:
        working_items = _prepare_new_items(normalized_items)
        skipped_success = 0

    writer = checkpoint_writer or write_json_atomic
    writer(output, working_items)
    model_calls = 0
    for item in working_items:
        if (
            item.get("type") != "text"
            or item.get("translation_status") == "success"
        ):
            continue
        model_calls += _translate_one(item, translator, max_retries)
        writer(output, working_items)

    text_items = [
        item for item in working_items if item.get("type") == "text"
    ]
    status_counts = Counter(
        item.get("translation_status") for item in text_items
    )
    pending_count = status_counts.get("pending", 0)
    if pending_count:
        raise TranslationContentError("正式翻译结束后仍存在 pending 对象")
    return TranslationStats(
        text_count=len(text_items),
        model_call_count=model_calls,
        skipped_success_count=skipped_success,
        success_count=status_counts.get("success", 0),
        failed_count=status_counts.get("failed", 0),
        pending_count=pending_count,
    )
```

- [ ] **Step 6: Run translation tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation.py -v
```

Expected: all formal translation, retry, resume, interruption, mismatch, and atomic-write tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: add resumable full translation"
```

---

### Task 3: Shared Workflow and Translation-Only CLI

**Files:**
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_cli.py`
- Modify: `src/pdf_trans/workflow.py`
- Modify: `src/pdf_trans/__main__.py`

**Interfaces:**
- Consumes:
  - `process_pdf(pdf_path, ..., translator=None, translation_max_retries=None)`;
  - `process_translation_file(normalized_path, *, translator=None, max_retries=None)`.
- Produces:
  - `TranslationFileResult(normalized_path, translated_path, stats)`;
  - `WorkflowResult.translated_path`;
  - `WorkflowResult.translation_stats`;
  - `python -m pdf_trans --translate-only normalized_content_list.json`.

- [ ] **Step 1: Write failing translation-only workflow tests**

Add to `tests/test_workflow.py`:

```python
from pdf_trans.translation import TranslationStats
from pdf_trans.workflow import process_translation_file


def test_process_translation_file_uses_sibling_output_and_fake_translator(
    tmp_path,
):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text(
        json.dumps([{"type": "text", "text": "A"}]),
        encoding="utf-8",
    )
    translator = FakeTranslator()

    result = process_translation_file(
        normalized,
        translator=translator,
        max_retries=0,
    )

    assert result.normalized_path == normalized.resolve()
    assert result.translated_path == (
        tmp_path / "translated_content_list.json"
    ).resolve()
    assert result.stats.text_count == 1
    assert result.stats.model_call_count == 1
    assert translator.received == ["A"]


@pytest.mark.parametrize(
    ("filename", "create_file"),
    [
        ("missing.json", False),
        ("other.json", True),
    ],
)
def test_process_translation_file_rejects_invalid_source(
    tmp_path,
    filename,
    create_file,
):
    source = tmp_path / filename
    if create_file:
        source.write_text("[]", encoding="utf-8")

    with pytest.raises(WorkflowError, match="normalized_content_list.json"):
        process_translation_file(source, translator=FakeTranslator())
```

Update the complete PDF workflow test to assert:

```python
assert result.translation_stats == TranslationStats(
    text_count=1,
    model_call_count=1,
    skipped_success_count=0,
    success_count=1,
    failed_count=0,
    pending_count=0,
)
```

- [ ] **Step 2: Write failing CLI-mode and statistics tests**

In `tests/test_cli.py`, replace flat translation count fields with
`translation_stats=TranslationStats(...)`. Add:

```python
from pdf_trans.translation import TranslationStats
from pdf_trans.workflow import TranslationFileResult


def test_main_runs_translation_only_without_pdf_workflow(
    tmp_path,
    monkeypatch,
    capsys,
):
    normalized = tmp_path / "normalized_content_list.json"
    translated = tmp_path / "translated_content_list.json"
    received = {}

    def fake_translation(path):
        received["path"] = path
        return TranslationFileResult(
            normalized_path=normalized,
            translated_path=translated,
            stats=TranslationStats(
                text_count=5,
                model_call_count=3,
                skipped_success_count=3,
                success_count=4,
                failed_count=1,
                pending_count=0,
            ),
        )

    monkeypatch.setattr(cli, "process_translation_file", fake_translation)
    monkeypatch.setattr(
        cli,
        "process_pdf",
        lambda *args, **kwargs: pytest.fail("PDF workflow must not run"),
    )

    exit_code = cli.main(["--translate-only", str(normalized)])

    assert exit_code == 0
    assert received["path"] == normalized
    assert capsys.readouterr().out == (
        "text 对象总数：5\n"
        "本次模型调用数量：3\n"
        "跳过的已成功数量：3\n"
        "翻译成功数量：4\n"
        "翻译失败数量：1\n"
        "待翻译数量：0\n"
        f"翻译文件：{translated}\n"
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["paper.pdf", "--translate-only", "normalized_content_list.json"],
    ],
)
def test_main_requires_exactly_one_input_mode(argv):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == 2
```

- [ ] **Step 3: Run workflow and CLI tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: FAIL because translation-only workflow, new statistics objects, and CLI mode selection do not exist.

- [ ] **Step 4: Implement shared workflow execution**

In `src/pdf_trans/workflow.py`, import `TranslationStats` and add:

```python
@dataclass(frozen=True)
class TranslationFileResult:
    normalized_path: Path
    translated_path: Path
    stats: TranslationStats


def validate_normalized_path(normalized_path: Path) -> Path:
    resolved = normalized_path.expanduser().resolve()
    if (
        not resolved.exists()
        or not resolved.is_file()
        or resolved.name != "normalized_content_list.json"
    ):
        raise WorkflowError(
            "翻译输入必须是存在的 normalized_content_list.json 普通文件："
            f"{normalized_path}"
        )
    return resolved


def _run_translation(
    normalized_path: Path,
    *,
    translator: TextTranslator | None,
    max_retries: int | None,
) -> TranslationFileResult:
    translated_path = normalized_path.with_name(
        "translated_content_list.json"
    )
    if translator is None:
        with OpenAICompatibleTranslator.from_env() as client:
            stats = translate_content_list_file(
                normalized_path,
                translated_path,
                client,
                max_retries=client.max_retries,
            )
    else:
        stats = translate_content_list_file(
            normalized_path,
            translated_path,
            translator,
            max_retries=1 if max_retries is None else max_retries,
        )
    return TranslationFileResult(
        normalized_path=normalized_path.resolve(),
        translated_path=translated_path.resolve(),
        stats=stats,
    )


def process_translation_file(
    normalized_path: Path,
    *,
    translator: TextTranslator | None = None,
    max_retries: int | None = None,
) -> TranslationFileResult:
    resolved = validate_normalized_path(normalized_path)
    return _run_translation(
        resolved,
        translator=translator,
        max_retries=max_retries,
    )
```

Change `WorkflowResult` to contain:

```python
translated_path: Path
translation_stats: TranslationStats
```

Change `process_pdf` signature:

```python
def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
    translator: TextTranslator | None = None,
    translation_max_retries: int | None = None,
) -> WorkflowResult:
```

After normalized output is written, call `_run_translation` and put its path
and stats into `WorkflowResult`.

- [ ] **Step 5: Implement CLI input selection and summary**

In `src/pdf_trans/__main__.py`, make `pdf_path` optional and add:

```python
parser.add_argument(
    "pdf_path",
    type=Path,
    nargs="?",
    help="要解析的 PDF 文件路径",
)
parser.add_argument(
    "--translate-only",
    type=Path,
    help="只翻译指定的 normalized_content_list.json，并支持断点续跑",
)
```

Add:

```python
def _print_translation_summary(
    stats: TranslationStats,
    output_path: Path,
) -> None:
    print(f"text 对象总数：{stats.text_count}")
    print(f"本次模型调用数量：{stats.model_call_count}")
    print(f"跳过的已成功数量：{stats.skipped_success_count}")
    print(f"翻译成功数量：{stats.success_count}")
    print(f"翻译失败数量：{stats.failed_count}")
    print(f"待翻译数量：{stats.pending_count}")
    print(f"翻译文件：{output_path}")
```

In `main`, validate exactly one input mode:

```python
parser = build_parser()
args = parser.parse_args(argv)
if (args.pdf_path is None) == (args.translate_only is None):
    parser.error("必须提供 PDF 路径或 --translate-only，且只能提供一个")

try:
    if args.translate_only is not None:
        translation_result = process_translation_file(args.translate_only)
        _print_translation_summary(
            translation_result.stats,
            translation_result.translated_path,
        )
        return 0
    result = process_pdf(args.pdf_path, svr_url=args.svr_url)
except (PDFTransError, OSError) as exc:
    print(f"错误：{exc}", file=sys.stderr)
    return 1
```

After existing PDF-stage output, call `_print_translation_summary` with
`result.translation_stats` and `result.translated_path`.

- [ ] **Step 6: Run workflow and CLI tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: all workflow and CLI tests PASS without real service calls.

- [ ] **Step 7: Commit**

```bash
git add src/pdf_trans/workflow.py src/pdf_trans/__main__.py tests/test_workflow.py tests/test_cli.py
git commit -m "feat: add translation-only resume command"
```

---

### Task 4: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Verify: `src/pdf_trans/`, `tests/`, and active documentation.

**Interfaces:**
- Consumes: completed full translation and resume behavior.
- Produces: user instructions for first run, resume, configuration, output states, and statistics.

- [ ] **Step 1: Replace sample documentation with formal usage**

Document both commands:

```bash
python -m pdf_trans /path/to/document.pdf

python -m pdf_trans --translate-only \
  /path/to/normalized_content_list.json
```

Document:

```bash
export TRANSLATION_TIMEOUT_SECONDS="60"
export TRANSLATION_MAX_RETRIES="1"
```

Explain that all text objects are processed sequentially, success is skipped on
resume, pending and failed are attempted, every completed object is atomically
saved, and a normal finish has zero pending objects.

Show the exact final output:

```text
text 对象总数：<text_count>
本次模型调用数量：<model_call_count>
跳过的已成功数量：<skipped_success_count>
翻译成功数量：<success_count>
翻译失败数量：<failed_count>
待翻译数量：<pending_count>
翻译文件：<absolute path>
```

- [ ] **Step 2: Run static scope checks**

Run:

```bash
rg -n "SAMPLE_LIMIT|sample_limit|sample 翻译|前 3 个|超出前 3 个" \
  src/pdf_trans tests README.md \
  docs/superpowers/specs/2026-07-26-paper-translation-design.md
git diff --check
```

Expected: no sample-limit implementation or active documentation matches; diff check exits successfully.

- [ ] **Step 3: Run the complete test suite and help smoke tests**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m pdf_trans --help
```

Expected: all tests PASS; help shows the optional PDF path and
`--translate-only TRANSLATE_ONLY`.

- [ ] **Step 4: Verify test isolation from real network calls**

Run:

```bash
rg -n "MockTransport|FakeTranslator|monkeypatch" tests/test_translation.py \
  tests/test_translation_client.py tests/test_workflow.py
```

Expected: translation client requests use `MockTransport`, and workflow/file
tests inject fakes.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md
git commit -m "docs: document resumable full translation"
```
