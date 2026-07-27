# Concurrent Translation and Workflow Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded multithreaded LLM translation with a default concurrency of 5, a default timeout of 120 seconds, and detailed color-coded timing logs throughout both supported workflows.

**Architecture:** Keep the synchronous OpenAI-compatible client and dispatch independent text translations through `ThreadPoolExecutor`. Worker threads return immutable outcomes; only the main thread updates the ordered object list and atomically checkpoints it. Add one focused logging module for ANSI level prefixes and stage timing, then instrument translation, workflow, and CLI boundaries without logging source text, translated text, or credentials.

**Tech Stack:** Python 3.11+, `concurrent.futures`, `threading`, standard-library `logging`, `time.perf_counter`, `httpx`, `pytest`.

## Global Constraints

- Use `TRANSLATION_CONCURRENCY`; default `5`; accept only integers greater than 0.
- Change the default `TRANSLATION_TIMEOUT_SECONDS` from 60 seconds to 120 seconds.
- Keep `TRANSLATION_MAX_RETRIES=1` as one additional attempt after the initial call.
- Send one `type=text` object per LLM request; do not batch or split text.
- Do not add `reasoning`, `reasoning_effort`, or `thinking` to the compatible request payload.
- Preserve every input object, object count, original array order, original `text`, and all non-translation fields.
- Skip resumed `success`; process resumed `pending` and `failed`.
- Only the main thread may mutate the output list and atomically write `translated_content_list.json`.
- Atomically checkpoint once before dispatch and after every completed text outcome.
- Keep ordinary single-object failures isolated; a final failure must not stop other translations.
- Log INFO with a green prefix, WARN with a yellow prefix, and ERROR with a red prefix.
- Log operational descriptions and elapsed time for every workflow stage.
- Never log API keys, complete source text, or complete translated text.
- Keep final translation statistics on stdout and operational logs on stderr.
- Do not add asyncio, multiprocessing, databases, Agent behavior, Web APIs, long-text splitting, table translation, caption translation, or real model calls in tests.

---

## File Map

- Create `src/pdf_trans/logging_utils.py`: ANSI level formatter, logger configuration, and reusable stage timer.
- Create `tests/test_logging_utils.py`: formatter and stage-timing behavior.
- Modify `src/pdf_trans/translation_client.py`: timeout default, concurrency configuration, and request contract.
- Modify `tests/test_translation_client.py`: configuration validation and absence of reasoning fields.
- Modify `src/pdf_trans/translation.py`: immutable outcomes, worker retry logic, thread-pool scheduling, serialized checkpoint writes, and per-segment logs.
- Modify `tests/test_translation.py`: deterministic sequential tests plus true concurrency, ordering, checkpoint ownership, retries, resume, interruption, and log tests.
- Modify `src/pdf_trans/workflow.py`: concurrency propagation and stage/cross-page logging.
- Modify `tests/test_workflow.py`: concurrency propagation and workflow log coverage.
- Modify `src/pdf_trans/__main__.py`: install color logging and report expected errors through the logger.
- Modify `tests/test_cli.py`: colored error output while preserving stdout statistics.
- Modify `README.md`: new environment variable, 120-second default, concurrency behavior, and log examples.
- Modify `pyproject.toml`: remove the stale “sample” wording from the package description.

---

### Task 1: Translation Configuration and No-Reasoning Contract

**Files:**
- Modify: `tests/test_translation_client.py`
- Modify: `src/pdf_trans/translation_client.py`

**Interfaces:**
- Consumes: existing `OpenAICompatibleTranslator.from_env()` and `translate(text: str) -> str`.
- Produces: `DEFAULT_TRANSLATION_TIMEOUT_SECONDS = 120.0`, `DEFAULT_TRANSLATION_CONCURRENCY = 5`, `OpenAICompatibleTranslator.concurrency: int`.

- [ ] **Step 1: Add failing tests for the new defaults and environment parsing**

Add these imports and tests to `tests/test_translation_client.py`:

```python
from pdf_trans.translation_client import (
    DEFAULT_TRANSLATION_CONCURRENCY,
    DEFAULT_TRANSLATION_MAX_RETRIES,
    DEFAULT_TRANSLATION_TIMEOUT_SECONDS,
    OpenAICompatibleTranslator,
    TRANSLATION_SYSTEM_PROMPT,
)


def test_from_env_uses_new_timeout_and_concurrency_defaults(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.delenv("TRANSLATION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TRANSLATION_MAX_RETRIES", raising=False)
    monkeypatch.delenv("TRANSLATION_CONCURRENCY", raising=False)

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.timeout_seconds == 120.0
    assert translator.timeout_seconds == DEFAULT_TRANSLATION_TIMEOUT_SECONDS
    assert translator.max_retries == DEFAULT_TRANSLATION_MAX_RETRIES
    assert translator.concurrency == DEFAULT_TRANSLATION_CONCURRENCY == 5
    translator.close()


def test_from_env_reads_concurrency(monkeypatch):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://translate.example/v1")
    monkeypatch.setenv("TRANSLATION_API_KEY", "secret")
    monkeypatch.setenv("TRANSLATION_MODEL", "paper-model")
    monkeypatch.setenv("TRANSLATION_CONCURRENCY", "8")

    translator = OpenAICompatibleTranslator.from_env()

    assert translator.concurrency == 8
    translator.close()


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "1.5", "not-an-integer", "01"],
)
def test_from_env_rejects_invalid_concurrency(monkeypatch, value):
    monkeypatch.setenv("TRANSLATION_BASE_URL", "configured")
    monkeypatch.setenv("TRANSLATION_API_KEY", "configured")
    monkeypatch.setenv("TRANSLATION_MODEL", "configured")
    monkeypatch.setenv("TRANSLATION_CONCURRENCY", value)

    with pytest.raises(
        TranslationConfigError,
        match="TRANSLATION_CONCURRENCY",
    ):
        OpenAICompatibleTranslator.from_env()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_translation_client.py::test_from_env_uses_new_timeout_and_concurrency_defaults \
  tests/test_translation_client.py::test_from_env_reads_concurrency \
  tests/test_translation_client.py::test_from_env_rejects_invalid_concurrency \
  -v
```

Expected: collection or assertion failures because the concurrency constant/property does not exist and the timeout still defaults to 60.

- [ ] **Step 3: Add a failing assertion that no deep-thinking field is sent**

In `test_translate_sends_compatible_request_and_returns_only_content`, after checking
`seen["payload"]`, add:

```python
    assert set(seen["payload"]) == {"model", "messages"}
    assert "reasoning" not in seen["payload"]
    assert "reasoning_effort" not in seen["payload"]
    assert "thinking" not in seen["payload"]
```

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_translation_client.py::test_translate_sends_compatible_request_and_returns_only_content \
  -v
```

Expected: PASS against the current client, documenting and locking the existing no-reasoning behavior before the configuration refactor.

- [ ] **Step 4: Implement timeout and concurrency configuration**

In `src/pdf_trans/translation_client.py`:

```python
DEFAULT_TRANSLATION_TIMEOUT_SECONDS = 120.0
DEFAULT_TRANSLATION_MAX_RETRIES = 1
DEFAULT_TRANSLATION_CONCURRENCY = 5


def _parse_positive_integer(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise TranslationConfigError(
            f"{name} 必须是大于 0 的整数"
        ) from exc
    if parsed <= 0 or str(parsed) != value.strip():
        raise TranslationConfigError(f"{name} 必须是大于 0 的整数")
    return parsed
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
        concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY,
        http_client: httpx.Client | None = None,
    ) -> None:
        if concurrency <= 0:
            raise TranslationConfigError(
                "TRANSLATION_CONCURRENCY 必须是大于 0 的整数"
            )
        self.concurrency = concurrency
```

Parse and pass the environment setting in `from_env()`:

```python
        concurrency = _parse_positive_integer(
            "TRANSLATION_CONCURRENCY",
            os.environ.get(
                "TRANSLATION_CONCURRENCY",
                str(DEFAULT_TRANSLATION_CONCURRENCY),
            ),
        )

        return cls(
            base_url=values["TRANSLATION_BASE_URL"],
            api_key=values["TRANSLATION_API_KEY"],
            model=values["TRANSLATION_MODEL"],
            timeout_seconds=timeout,
            max_retries=max_retries,
            concurrency=concurrency,
        )
```

Keep the request JSON limited to `model` and `messages`.

- [ ] **Step 5: Run all translation-client tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation_client.py -v
```

Expected: all translation-client tests pass without a real HTTP request.

- [ ] **Step 6: Commit the configuration contract**

```bash
git add src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: configure translation concurrency"
```

---

### Task 2: Color Logger and Reusable Stage Timing

**Files:**
- Create: `src/pdf_trans/logging_utils.py`
- Create: `tests/test_logging_utils.py`

**Interfaces:**
- Consumes: Python standard-library `logging`, `sys.stderr`, and `time.perf_counter`.
- Produces: `configure_logging(stream: TextIO | None = None) -> None`, `ColorLevelFormatter`, and `logged_stage(logger, name, action) -> StageLog`.

- [ ] **Step 1: Write failing formatter tests**

Create `tests/test_logging_utils.py`:

```python
import io
import logging

import pytest

from pdf_trans.logging_utils import configure_logging


@pytest.fixture(autouse=True)
def reset_package_logger():
    package_logger = logging.getLogger("pdf_trans")
    yield
    package_logger.handlers.clear()
    package_logger.setLevel(logging.NOTSET)
    package_logger.propagate = True


@pytest.mark.parametrize(
    ("level", "prefix"),
    [
        (logging.INFO, "\033[32m[INFO]\033[0m"),
        (logging.WARNING, "\033[33m[WARN]\033[0m"),
        (logging.ERROR, "\033[31m[ERROR]\033[0m"),
    ],
)
def test_configured_logger_uses_colored_level_prefix(level, prefix):
    stream = io.StringIO()
    configure_logging(stream)
    logger = logging.getLogger("pdf_trans.test")

    logger.log(level, "消息")

    assert stream.getvalue() == f"{prefix} 消息\n"


def test_configure_logging_replaces_handlers_instead_of_duplicating():
    first = io.StringIO()
    second = io.StringIO()
    configure_logging(first)
    configure_logging(second)

    logging.getLogger("pdf_trans.test").info("一次")

    assert first.getvalue() == ""
    assert second.getvalue().count("一次") == 1
```

- [ ] **Step 2: Run formatter tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_logging_utils.py -v
```

Expected: collection fails because `pdf_trans.logging_utils` does not exist.

- [ ] **Step 3: Implement the color formatter and logger configuration**

Create `src/pdf_trans/logging_utils.py` with:

```python
from __future__ import annotations

import logging
import sys
import time
from types import TracebackType
from typing import TextIO

PACKAGE_LOGGER_NAME = "pdf_trans"

_PREFIXES = {
    logging.INFO: "\033[32m[INFO]\033[0m",
    logging.WARNING: "\033[33m[WARN]\033[0m",
    logging.ERROR: "\033[31m[ERROR]\033[0m",
    logging.CRITICAL: "\033[31m[ERROR]\033[0m",
}


class ColorLevelFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        prefix = _PREFIXES.get(record.levelno, f"[{record.levelname}]")
        return f"{prefix} {record.getMessage()}"


def configure_logging(stream: TextIO | None = None) -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(ColorLevelFormatter())
    package_logger.handlers.clear()
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False
```

- [ ] **Step 4: Add failing stage-timing tests**

Append to `tests/test_logging_utils.py`:

```python
from pdf_trans.logging_utils import logged_stage


def test_logged_stage_reports_action_result_and_elapsed_time(
    monkeypatch,
    caplog,
):
    times = iter([10.0, 12.5])
    monkeypatch.setattr(
        "pdf_trans.logging_utils.time.perf_counter",
        lambda: next(times),
    )
    logger = logging.getLogger("pdf_trans.stage-test")
    caplog.set_level(logging.INFO, logger=logger.name)

    with logged_stage(logger, "清洗数据", "移除页眉和空 text") as stage:
        stage.set_result("输入 10 项，过滤 2 项，保留 8 项")

    messages = [record.getMessage() for record in caplog.records]
    assert "开始清洗数据：移除页眉和空 text" in messages
    assert (
        "清洗数据完成：输入 10 项，过滤 2 项，保留 8 项，耗时 2.50 秒"
        in messages
    )


def test_logged_stage_reports_error_and_elapsed_time(monkeypatch, caplog):
    times = iter([3.0, 4.25])
    monkeypatch.setattr(
        "pdf_trans.logging_utils.time.perf_counter",
        lambda: next(times),
    )
    logger = logging.getLogger("pdf_trans.stage-error-test")
    caplog.set_level(logging.ERROR, logger=logger.name)

    with pytest.raises(RuntimeError, match="boom"):
        with logged_stage(logger, "翻译", "并发调用模型"):
            raise RuntimeError("boom")

    assert any(
        record.levelno == logging.ERROR
        and record.getMessage() == "翻译失败：boom，耗时 1.25 秒"
        for record in caplog.records
    )
```

- [ ] **Step 5: Run stage tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_logging_utils.py -v
```

Expected: import or attribute failure because `logged_stage` and `StageLog` do not exist.

- [ ] **Step 6: Implement `StageLog`**

Append this implementation to `src/pdf_trans/logging_utils.py`:

```python
class StageLog:
    def __init__(
        self,
        logger: logging.Logger,
        name: str,
        action: str,
    ) -> None:
        self.logger = logger
        self.name = name
        self.action = action
        self._started = 0.0
        self._result = ""

    def set_result(self, result: str) -> None:
        self._result = result

    def __enter__(self) -> "StageLog":
        self._started = time.perf_counter()
        self.logger.info("开始%s：%s", self.name, self.action)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        elapsed = time.perf_counter() - self._started
        if exc is not None:
            self.logger.error(
                "%s失败：%s，耗时 %.2f 秒",
                self.name,
                exc,
                elapsed,
            )
            return False
        detail = f"：{self._result}" if self._result else ""
        self.logger.info(
            "%s完成%s，耗时 %.2f 秒",
            self.name,
            detail,
            elapsed,
        )
        return False


def logged_stage(
    logger: logging.Logger,
    name: str,
    action: str,
) -> StageLog:
    return StageLog(logger, name, action)
```

- [ ] **Step 7: Run logging tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_logging_utils.py -v
```

Expected: all logger and stage-timing tests pass.

- [ ] **Step 8: Commit logging infrastructure**

```bash
git add src/pdf_trans/logging_utils.py tests/test_logging_utils.py
git commit -m "feat: add colored stage logging"
```

---

### Task 3: Multithreaded Translation Engine and Segment Logs

**Files:**
- Modify: `tests/test_translation.py`
- Modify: `src/pdf_trans/translation.py`

**Interfaces:**
- Consumes: `logged_stage`, `TextTranslator.translate`, existing resume validation, and `write_json_atomic`.
- Produces: `TranslationOutcome`, `translate_content_list_file(source, output, translator, *, max_retries=1, concurrency=5, checkpoint_writer=None)`, serialized checkpoint application, and per-segment logging.

- [ ] **Step 1: Make existing order-sensitive tests explicitly sequential**

For existing tests using iterator-based `FakeTranslator`, pass `concurrency=1`:

```python
    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )
```

Add `concurrency=1` to the calls in:

- `test_translate_file_processes_every_text_object_and_preserves_non_text`;
- `test_retries_failures_and_continues_with_model_call_count`;
- `test_resume_skips_success_and_retries_pending_and_failed`;
- `test_interruption_leaves_checkpoint_and_rerun_skips_completed`;
- `test_custom_checkpoint_writer_receives_each_completed_state`.

- [ ] **Step 2: Add a failing test proving real concurrent execution**

Add imports and a thread-safe probe to `tests/test_translation.py`:

```python
import threading


class ConcurrentProbeTranslator:
    def __init__(self, participants):
        self.barrier = threading.Barrier(participants)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def translate(self, text):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait(timeout=2)
            return f"译文：{text}"
        finally:
            with self.lock:
                self.active -= 1


def test_translate_file_runs_up_to_configured_concurrency(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [{"type": "text", "text": value} for value in ("A", "B", "C")]
        ),
        encoding="utf-8",
    )
    translator = ConcurrentProbeTranslator(participants=3)

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=3,
    )

    assert translator.max_active == 3
    assert stats == TranslationStats(3, 3, 0, 3, 0, 0)
```

- [ ] **Step 3: Run the concurrency test and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_translation.py::test_translate_file_runs_up_to_configured_concurrency \
  -v
```

Expected: `TypeError` because `translate_content_list_file` does not accept `concurrency`.

- [ ] **Step 4: Add failing tests for out-of-order completion and main-thread checkpoints**

Append:

```python
import time


class DelayedTranslator:
    def translate(self, text):
        delay = {"slow": 0.05, "fast": 0.01}[text]
        time.sleep(delay)
        return f"译文：{text}"


def test_out_of_order_completion_preserves_array_order(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "slow"},
                {"type": "image", "img_path": "a.png"},
                {"type": "text", "text": "fast"},
            ]
        ),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        DelayedTranslator(),
        concurrency=2,
    )

    result = read_items(output)
    assert [item["type"] for item in result] == ["text", "image", "text"]
    assert result[0]["translated_text"] == "译文：slow"
    assert result[1] == {"type": "image", "img_path": "a.png"}
    assert result[2]["translated_text"] == "译文：fast"
    assert stats.model_call_count == 2


def test_checkpoint_writer_runs_only_on_main_thread(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [{"type": "text", "text": value} for value in ("A", "B", "C")]
        ),
        encoding="utf-8",
    )
    main_thread = threading.get_ident()
    writer_threads = []
    checkpoints = []

    def writer(path, items):
        writer_threads.append(threading.get_ident())
        checkpoints.append(copy.deepcopy(items))
        path.write_text(json.dumps(items), encoding="utf-8")

    translate_content_list_file(
        source,
        output,
        ConcurrentProbeTranslator(participants=3),
        concurrency=3,
        checkpoint_writer=writer,
    )

    assert writer_threads == [main_thread] * 4
    assert len(checkpoints) == 4
    assert all(
        item.get("translation_status") == "pending"
        for item in checkpoints[0]
    )
    assert all(
        item.get("translation_status") == "success"
        for item in checkpoints[-1]
    )
```

- [ ] **Step 5: Introduce immutable worker outcomes**

In `src/pdf_trans/translation.py`, add imports:

```python
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Literal

from pdf_trans.logging_utils import logged_stage
from pdf_trans.translation_client import DEFAULT_TRANSLATION_CONCURRENCY

LOGGER = logging.getLogger(__name__)
```

Add:

```python
@dataclass(frozen=True)
class TranslationOutcome:
    index: int
    section_number: int
    status: Literal["success", "failed"]
    translated_text: str | None
    error: str | None
    model_call_count: int
    elapsed_seconds: float
```

Replace mutating `_translate_one` with a pure worker:

```python
def _translate_one(
    index: int,
    section_number: int,
    text: str,
    translator: TextTranslator,
    max_retries: int,
) -> TranslationOutcome:
    started = time.perf_counter()
    last_error: Exception | None = None
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        LOGGER.info(
            "第 %d 段开始翻译：第 %d/%d 次调用",
            section_number,
            attempt,
            total_attempts,
        )
        try:
            translated = translator.translate(text)
            if not isinstance(translated, str) or not translated.strip():
                raise ValueError("模型返回空译文")
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts:
                LOGGER.warning(
                    "第 %d 段第 %d 次调用失败：%s，将重试",
                    section_number,
                    attempt,
                    exc,
                )
                continue
            elapsed = time.perf_counter() - started
            error = str(exc) or exc.__class__.__name__
            LOGGER.error(
                "第 %d 段翻译完成：failed，耗时 %.2f 秒，错误：%s",
                section_number,
                elapsed,
                error,
            )
            return TranslationOutcome(
                index=index,
                section_number=section_number,
                status="failed",
                translated_text=None,
                error=error,
                model_call_count=attempt,
                elapsed_seconds=elapsed,
            )
        elapsed = time.perf_counter() - started
        LOGGER.info(
            "第 %d 段翻译完成：success，耗时 %.2f 秒，译文 %d 字符",
            section_number,
            elapsed,
            len(translated),
        )
        return TranslationOutcome(
            index=index,
            section_number=section_number,
            status="success",
            translated_text=translated,
            error=None,
            model_call_count=attempt,
            elapsed_seconds=elapsed,
        )

    raise AssertionError(f"第 {section_number} 段未产生翻译结果：{last_error}")
```

- [ ] **Step 6: Add main-thread outcome application**

Add:

```python
def _apply_outcome(
    item: dict[str, Any],
    outcome: TranslationOutcome,
) -> None:
    if outcome.status == "success":
        item["translated_text"] = outcome.translated_text
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        return
    item["translated_text"] = None
    item["translation_status"] = "failed"
    item["translation_error"] = outcome.error


def _collect_translation_work(
    items: list[dict[str, Any]],
) -> list[tuple[int, int, str]]:
    work = []
    section_number = 0
    for index, item in enumerate(items):
        if item.get("type") != "text":
            continue
        section_number += 1
        if item.get("translation_status") == "success":
            continue
        work.append((index, section_number, item["text"]))
    return work
```

- [ ] **Step 7: Replace the sequential loop with serialized concurrent collection**

Extend the public signature:

```python
def translate_content_list_file(
    source: Path,
    output: Path,
    translator: TextTranslator,
    *,
    max_retries: int = 1,
    concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY,
    checkpoint_writer: CheckpointWriter | None = None,
) -> TranslationStats:
```

Validate `type(concurrency) is int and concurrency > 0`; otherwise raise
`TranslationContentError("concurrency 必须是大于 0 的整数")`.

Wrap input/断点 preparation and the initial checkpoint:

```python
    checkpoint_action = (
        "校验已有 translated_content_list.json"
        if output.exists()
        else "创建新的 translated_content_list.json"
    )
    with logged_stage(
        LOGGER,
        "准备翻译断点",
        checkpoint_action,
    ) as checkpoint_stage:
        normalized = _read_object_array(source, "规范化内容")
        if output.exists():
            if not output.is_file():
                raise TranslationContentError("断点文件不是普通文件")
            existing = _read_object_array(output, "断点翻译结果")
            items, skipped_success = _prepare_resumed_items(
                normalized,
                existing,
            )
        else:
            items = _prepare_new_items(normalized)
            skipped_success = 0
        writer = checkpoint_writer or write_json_atomic
        writer(output, items)
        checkpoint_stage.set_result(
            f"text 共 {sum(item.get('type') == 'text' for item in items)} 段，"
            f"跳过已有 success {skipped_success} 段"
        )
```

After the initial checkpoint, replace the sequential loop with:

```python
    work = _collect_translation_work(items)
    model_calls = 0
    LOGGER.info(
        "准备并发翻译：待处理 %d 段，并发数 %d",
        len(work),
        concurrency,
    )
    executor = ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="pdf-trans",
    )
    futures: list[Future[TranslationOutcome]] = [
        executor.submit(
            _translate_one,
            index,
            section_number,
            text,
            translator,
            max_retries,
        )
        for index, section_number, text in work
    ]
    try:
        for future in as_completed(futures):
            outcome = future.result()
            _apply_outcome(items[outcome.index], outcome)
            model_calls += outcome.model_call_count
            writer(output, items)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
```

Do not mutate `items` from `_translate_one`.

- [ ] **Step 8: Run concurrency and existing translation tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation.py -v
```

Expected: all existing sequential behavior, concurrency, ordering, resume, retry, interruption, and checkpoint tests pass.

- [ ] **Step 9: Add failing segment-log assertions**

Add:

```python
def test_translation_logs_attempt_retry_and_safe_success_summary(
    tmp_path,
    caplog,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source_text = "sensitive source paragraph"
    translated_text = "保密的完整译文"
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="pdf_trans.translation")

    translate_content_list_file(
        source,
        output,
        FakeTranslator([RuntimeError("timeout"), translated_text]),
        max_retries=1,
        concurrency=1,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "第 1 段开始翻译：第 1/2 次调用" in messages
    assert "第 1 段第 1 次调用失败：timeout，将重试" in messages
    assert "第 1 段翻译完成：success" in messages
    assert f"译文 {len(translated_text)} 字符" in messages
    assert source_text not in messages
    assert translated_text not in messages
```

- [ ] **Step 10: Run segment-log and full translation tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_translation.py -v
```

Expected: all translation tests pass and no test calls a real model endpoint.

- [ ] **Step 11: Commit concurrent translation**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "feat: translate text objects concurrently"
```

---

### Task 4: Workflow Concurrency Wiring and Detailed Stage Logs

**Files:**
- Modify: `tests/test_workflow.py`
- Modify: `src/pdf_trans/workflow.py`

**Interfaces:**
- Consumes: `OpenAICompatibleTranslator.concurrency`, concurrent `translate_content_list_file`, and `logged_stage`.
- Produces: `process_translation_file(normalized_path, *, translator=None, max_retries=None, concurrency=None)` and `process_pdf(pdf_path, *, svr_url=DEFAULT_SVR_URL, data_dir=None, client=None, translator=None, translation_max_retries=None, translation_concurrency=None)` plus stage and cross-page logs.

- [ ] **Step 1: Add failing concurrency-propagation tests**

Add to `tests/test_workflow.py`:

```python
def test_process_translation_file_passes_explicit_concurrency(
    tmp_path,
    monkeypatch,
):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    received = {}

    def fake_translate(source, output, translator, **kwargs):
        received.update(kwargs)
        return TranslationStats(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(
        "pdf_trans.workflow.translate_content_list_file",
        fake_translate,
    )

    process_translation_file(
        normalized,
        translator=FakeTranslator(),
        max_retries=2,
        concurrency=7,
    )

    assert received == {"max_retries": 2, "concurrency": 7}
```

Add a context-client test proving environment-derived values are forwarded:

```python
def test_process_translation_file_uses_client_retry_and_concurrency(
    tmp_path,
    monkeypatch,
):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    received = {}

    class ContextTranslator:
        max_retries = 3
        concurrency = 4

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def translate(self, text):
            return text

    monkeypatch.setattr(
        "pdf_trans.workflow.OpenAICompatibleTranslator.from_env",
        lambda: ContextTranslator(),
    )

    def fake_translate(source, output, translator, **kwargs):
        received.update(kwargs)
        return TranslationStats(0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(
        "pdf_trans.workflow.translate_content_list_file",
        fake_translate,
    )

    process_translation_file(normalized)

    assert received == {"max_retries": 3, "concurrency": 4}
```

- [ ] **Step 2: Run propagation tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_workflow.py::test_process_translation_file_passes_explicit_concurrency \
  tests/test_workflow.py::test_process_translation_file_uses_client_retry_and_concurrency \
  -v
```

Expected: `TypeError` for the unsupported `concurrency` argument or a missing forwarded value.

- [ ] **Step 3: Wire concurrency through workflow APIs**

Update `_run_translation`:

```python
def _run_translation(
    normalized_path: Path,
    *,
    translator: TextTranslator | None,
    max_retries: int | None,
    concurrency: int | None,
) -> TranslationFileResult:
```

For a real client:

```python
            retries = (
                translation_client.max_retries
                if max_retries is None
                else max_retries
            )
            workers = (
                translation_client.concurrency
                if concurrency is None
                else concurrency
            )
            stats = translate_content_list_file(
                normalized_path,
                translated_path,
                translation_client,
                max_retries=retries,
                concurrency=workers,
            )
```

For an injected translator, use
`DEFAULT_TRANSLATION_MAX_RETRIES` and `DEFAULT_TRANSLATION_CONCURRENCY` when
the overrides are absent.

Extend:

```python
def process_translation_file(
    normalized_path: Path,
    *,
    translator: TextTranslator | None = None,
    max_retries: int | None = None,
    concurrency: int | None = None,
) -> TranslationFileResult:
```

Extend:

```python
def process_pdf(
    pdf_path: Path,
    *,
    svr_url: str = DEFAULT_SVR_URL,
    data_dir: Path | None = None,
    client: PDFParser | None = None,
    translator: TextTranslator | None = None,
    translation_max_retries: int | None = None,
    translation_concurrency: int | None = None,
) -> WorkflowResult:
```

- [ ] **Step 4: Add failing tests for stage and cross-page logs**

Add imports:

```python
import logging
```

Add:

```python
def test_process_pdf_logs_stage_actions_counts_and_cross_page_merge(
    tmp_path,
    caplog,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "header", "text": "页眉"},
                {"type": "text", "text": "上一页未结束", "page_idx": 0},
                {"type": "text", "text": "下一页继续。", "page_idx": 1},
            ]
        )
    )
    caplog.set_level(logging.INFO)

    process_pdf(
        pdf,
        data_dir=tmp_path / "data",
        client=client,
        translator=FakeTranslator(),
        translation_concurrency=1,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    for stage in (
        "校验 PDF",
        "调用 MinerU",
        "解压解析结果",
        "清洗数据",
        "检测跨页段落",
        "合并跨页段落",
        "翻译 text 对象",
        "渲染 Markdown",
    ):
        assert f"开始{stage}" in messages
        assert f"{stage}完成" in messages
    assert "清洗数据完成：输入 3 项，过滤 1 项，保留 2 项" in messages
    assert "被分页分裂，将合并为一个段落" in messages
    assert "耗时 " in messages
```

- [ ] **Step 5: Run the workflow-log test and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_workflow.py::test_process_pdf_logs_stage_actions_counts_and_cross_page_merge \
  -v
```

Expected: failure because the workflow does not emit stage logs.

- [ ] **Step 6: Instrument the workflow**

Add to `src/pdf_trans/workflow.py`:

```python
import logging

from pdf_trans.logging_utils import logged_stage
from pdf_trans.translation_client import (
    DEFAULT_TRANSLATION_CONCURRENCY,
    DEFAULT_TRANSLATION_MAX_RETRIES,
    OpenAICompatibleTranslator,
)

LOGGER = logging.getLogger(__name__)
```

Add a helper that maps array indexes to 1-based text segment numbers:

```python
def _text_section_numbers(items: list[object]) -> dict[int, int]:
    result = {}
    section_number = 0
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("type") == "text":
            section_number += 1
            result[index] = section_number
    return result


def _log_cross_page_candidates(
    items: list[object],
    candidates: list[dict[str, object]],
) -> None:
    section_numbers = _text_section_numbers(items)
    for candidate in candidates:
        previous_index = candidate["previous_index"]
        next_index = candidate["next_index"]
        LOGGER.warning(
            "检测到第 %d 段（数组第 %d 项）和第 %d 段（数组第 %d 项）"
            "被分页分裂，将合并为一个段落",
            section_numbers[previous_index],
            previous_index + 1,
            section_numbers[next_index],
            next_index + 1,
        )
```

Wrap each existing stage with `logged_stage`. The cleaning stage must set its
result:

```python
    with logged_stage(
        LOGGER,
        "清洗数据",
        "移除 header、footer、page_number 和空 text",
    ) as stage:
        cleaned_items, stats = clean_content_list_file_with_items(
            source_path,
            output_path,
        )
        stage.set_result(
            f"输入 {stats.before_count} 项，过滤 {stats.filtered_count} 项，"
            f"保留 {stats.after_count} 项"
        )
```

After detection and before normalization:

```python
        _log_cross_page_candidates(cleaned_items, candidates)
        stage.set_result(f"发现 {len(candidates)} 个候选")
```

Implement the existing PDF body as `_process_pdf_stages` with explicit timed stages:

```python
def _process_pdf_stages(
    pdf_path: Path,
    *,
    svr_url: str,
    data_dir: Path | None,
    client: PDFParser | None,
    translator: TextTranslator | None,
    translation_max_retries: int | None,
    translation_concurrency: int | None,
) -> WorkflowResult:
    with logged_stage(
        LOGGER,
        "校验 PDF",
        "确认输入存在并且扩展名为 .pdf",
    ) as stage:
        resolved_pdf = validate_pdf_path(pdf_path)
        stage.set_result(f"输入文件 {resolved_pdf}")

    output_root = (data_dir or DEFAULT_DATA_DIR).resolve()
    with logged_stage(
        LOGGER,
        "调用 MinerU",
        "上传 PDF、轮询解析状态并下载 ZIP",
    ) as stage:
        if client is None:
            with MinerUClient(svr_url=svr_url) as mineru_client:
                archive_bytes = mineru_client.parse_pdf(resolved_pdf)
        else:
            archive_bytes = client.parse_pdf(resolved_pdf)
        stage.set_result(f"收到 {len(archive_bytes)} 字节 ZIP 数据")

    with logged_stage(
        LOGGER,
        "解压解析结果",
        f"解压 ZIP 到 {output_root} 并定位 content list",
    ) as stage:
        extracted_paths = extract_zip(archive_bytes, output_root)
        source_path = find_content_list(extracted_paths)
        stage.set_result(
            f"解压 {len(extracted_paths)} 个文件，content list 为 {source_path}"
        )

    output_path = source_path.parent / "cleaned_content_list.json"
    with logged_stage(
        LOGGER,
        "清洗数据",
        "移除 header、footer、page_number 和空 text",
    ) as stage:
        cleaned_items, stats = clean_content_list_file_with_items(
            source_path,
            output_path,
        )
        stage.set_result(
            f"输入 {stats.before_count} 项，过滤 {stats.filtered_count} 项，"
            f"保留 {stats.after_count} 项"
        )

    candidates_path = output_path.parent / "cross_page_candidates.json"
    with logged_stage(
        LOGGER,
        "检测跨页段落",
        "检查连续页面上的相邻 text 并写出候选报告",
    ) as stage:
        candidates = detect_cross_page_candidates(cleaned_items)
        _log_cross_page_candidates(cleaned_items, candidates)
        write_cross_page_candidates_file(candidates, candidates_path)
        stage.set_result(f"发现 {len(candidates)} 个候选")

    normalized_path = output_path.parent / "normalized_content_list.json"
    with logged_stage(
        LOGGER,
        "合并跨页段落",
        "按候选链合并 text 并写出规范化内容",
    ) as stage:
        normalized_items = normalize_cross_page_items(
            cleaned_items,
            candidates,
        )
        write_normalized_content_list_file(
            normalized_items,
            normalized_path,
        )
        stage.set_result(f"写出 {len(normalized_items)} 个对象")

    with logged_stage(
        LOGGER,
        "翻译 text 对象",
        "按配置的线程数逐段调用 OpenAI 兼容接口",
    ) as stage:
        translation_result = _run_translation(
            normalized_path,
            translator=translator,
            max_retries=translation_max_retries,
            concurrency=translation_concurrency,
        )
        stage.set_result(
            f"success {translation_result.stats.success_count} 段，"
            f"failed {translation_result.stats.failed_count} 段"
        )

    markdown_path = output_path.parent / "rendered.md"
    with logged_stage(
        LOGGER,
        "渲染 Markdown",
        "按规范化对象顺序生成 rendered.md",
    ) as stage:
        render_content_list_file(normalized_path, markdown_path)
        stage.set_result(f"输出文件 {markdown_path.resolve()}")

    return WorkflowResult(
        source_path=source_path,
        output_path=output_path.resolve(),
        normalized_path=normalized_path.resolve(),
        normalized_count=len(normalized_items),
        markdown_path=markdown_path.resolve(),
        candidates_path=candidates_path.resolve(),
        candidate_count=len(candidates),
        before_count=stats.before_count,
        filtered_count=stats.filtered_count,
        after_count=stats.after_count,
        content_stats=stats.content_stats,
        translated_path=translation_result.translated_path,
        translation_stats=translation_result.stats,
    )
```

Wrap the complete body of `process_pdf` in:

```python
    with logged_stage(
        LOGGER,
        "完整 PDF 工作流",
        f"解析、清洗、规范化并翻译 {pdf_path}",
    ) as workflow_stage:
        result = _process_pdf_stages(
            pdf_path,
            svr_url=svr_url,
            data_dir=data_dir,
            client=client,
            translator=translator,
            translation_max_retries=translation_max_retries,
            translation_concurrency=translation_concurrency,
        )
        workflow_stage.set_result(
            f"输出翻译文件 {result.translated_path}"
        )
        return result
```

Extract the existing stage body into the private `_process_pdf_stages` helper with the
listed arguments to avoid one deeply indented function. Implement the outer
`logged_stage` named `仅翻译工作流` in `process_translation_file` as follows:

```python
def process_translation_file(
    normalized_path: Path,
    *,
    translator: TextTranslator | None = None,
    max_retries: int | None = None,
    concurrency: int | None = None,
) -> TranslationFileResult:
    with logged_stage(
        LOGGER,
        "仅翻译工作流",
        f"从 {normalized_path} 校验断点并翻译全部 text",
    ) as workflow_stage:
        with logged_stage(
            LOGGER,
            "校验规范化文件",
            "确认文件存在且名称为 normalized_content_list.json",
        ) as validation_stage:
            resolved = validate_normalized_path(normalized_path)
            validation_stage.set_result(f"输入文件 {resolved}")
        with logged_stage(
            LOGGER,
            "翻译 text 对象",
            "按配置的线程数逐段调用 OpenAI 兼容接口",
        ) as translation_stage:
            result = _run_translation(
                resolved,
                translator=translator,
                max_retries=max_retries,
                concurrency=concurrency,
            )
            translation_stage.set_result(
                f"success {result.stats.success_count} 段，"
                f"failed {result.stats.failed_count} 段"
            )
        workflow_stage.set_result(
            f"输出翻译文件 {result.translated_path}"
        )
        return result
```

- [ ] **Step 7: Run workflow tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow.py -v
```

Expected: all workflow behavior, concurrency propagation, and log tests pass.

- [ ] **Step 8: Commit workflow instrumentation**

```bash
git add src/pdf_trans/workflow.py tests/test_workflow.py
git commit -m "feat: log timed workflow stages"
```

---

### Task 5: CLI Logging Configuration and Colored Errors

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/pdf_trans/__main__.py`

**Interfaces:**
- Consumes: `configure_logging()` and workflow loggers.
- Produces: green/yellow/red operational output on stderr while preserving final statistics on stdout.

- [ ] **Step 1: Write a failing colored-error CLI test**

Replace the error assertion in `test_main_reports_expected_error` with:

```python
    assert captured.err == (
        "\033[31m[ERROR]\033[0m 错误：PDF 文件不存在\n"
    )
```

Add:

```python
def test_main_configures_logging_without_changing_summary_stdout(
    tmp_path,
    monkeypatch,
    capsys,
):
    normalized = tmp_path / "normalized_content_list.json"

    def fake_process(path, *, translator=None, max_retries=None, concurrency=None):
        logging.getLogger("pdf_trans.workflow").info("工作流日志")
        return type(
            "Result",
            (),
            {
                "translated_path": tmp_path / "translated_content_list.json",
                "stats": TranslationStats(0, 0, 0, 0, 0, 0),
            },
        )()

    monkeypatch.setattr(cli, "process_translation_file", fake_process)

    assert cli.main(["--translate-only", str(normalized)]) == 0

    captured = capsys.readouterr()
    assert captured.err == "\033[32m[INFO]\033[0m 工作流日志\n"
    assert captured.out.startswith("text 对象总数：0\n")
```

- [ ] **Step 2: Run the CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_cli.py::test_main_reports_expected_error \
  tests/test_cli.py::test_main_configures_logging_without_changing_summary_stdout \
  -v
```

Expected: the error is not colored and the package logger has not been configured.

- [ ] **Step 3: Configure logging in the CLI and route expected errors**

In `src/pdf_trans/__main__.py`:

```python
import logging

from pdf_trans.logging_utils import configure_logging

LOGGER = logging.getLogger(__name__)
```

At the beginning of `main`, after parsing the mutually exclusive mode:

```python
    configure_logging()
```

Replace:

```python
        print(f"错误：{exc}", file=sys.stderr)
```

with:

```python
        LOGGER.error("错误：%s", exc)
```

Remove the unused `sys` import.

Update the translate-only fake function signatures in `tests/test_cli.py` to accept
`concurrency=None`, matching the workflow API.

- [ ] **Step 4: Run all CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: colored operational logs appear on stderr; all statistics remain unchanged on stdout.

- [ ] **Step 5: Commit CLI logging**

```bash
git add src/pdf_trans/__main__.py tests/test_cli.py
git commit -m "feat: add colored cli logging"
```

---

### Task 6: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Verify: all files under `src/pdf_trans/` and `tests/`

**Interfaces:**
- Consumes: completed environment, concurrency, logging, CLI, and resume behavior.
- Produces: current user documentation and a clean, fully tested branch.

- [ ] **Step 1: Update README configuration**

Document:

```bash
export TRANSLATION_TIMEOUT_SECONDS="120"
export TRANSLATION_MAX_RETRIES="1"
export TRANSLATION_CONCURRENCY="5"
```

Explain:

- timeout defaults to two minutes;
- max retries counts additional attempts;
- concurrency defaults to five simultaneous text-object requests;
- completion order may differ, but JSON order is unchanged;
- checkpoint writes are serialized by the main thread;
- rerunning `--translate-only` retains the existing resume validation.

- [ ] **Step 2: Document logging without exposing content**

Add README examples:

```text
[INFO] 开始翻译 text 对象：使用 5 个线程逐段调用模型
[INFO] 第 12 段翻译完成：success，耗时 3.42 秒，译文 286 字符
[WARN] 检测到第 18 段和第 19 段被分页分裂，将合并为一个段落
[ERROR] 第 20 段翻译完成：failed，耗时 240.13 秒，错误：请求超时
```

State that operational logs go to stderr, final statistics go to stdout, and complete
source/translated text and API keys are never logged.

- [ ] **Step 3: Remove stale package wording**

Change the `pyproject.toml` description to:

```toml
description = "Parse a PDF with MinerU, normalize its content list, and translate text objects."
```

- [ ] **Step 4: Run static checks**

Run:

```bash
git diff --check
rg -n "DEFAULT_TRANSLATION_TIMEOUT_SECONDS = 120.0" src/pdf_trans/translation_client.py
rg -n "DEFAULT_TRANSLATION_CONCURRENCY = 5" src/pdf_trans/translation_client.py
rg -n "reasoning|reasoning_effort|thinking" src/pdf_trans/translation_client.py
```

Expected:

- `git diff --check` has no output;
- timeout and concurrency constants each have one match;
- the production request client has no matches for reasoning/thinking fields.

- [ ] **Step 5: Prove tests contain no real translation endpoint**

Run:

```bash
rg -n "MockTransport|FakeTranslator|ConcurrentProbeTranslator|DelayedTranslator" tests
rg -n "api_key=.secret.|TRANSLATION_API_KEY.*configured" tests/test_translation_client.py
```

Expected: translation tests use fake translators or `httpx.MockTransport`; no test depends on the user's shell API key.

- [ ] **Step 6: Run the complete test suite**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: every test passes.

- [ ] **Step 7: Verify the CLI help and working tree**

Run:

```bash
.venv/bin/python -m pdf_trans --help
git status --short
```

Expected:

- help still shows optional `pdf_path`, `--translate-only`, and `--svr-url`;
- before the documentation commit, only `README.md` and `pyproject.toml` are uncommitted.

- [ ] **Step 8: Commit documentation**

```bash
git add README.md pyproject.toml
git commit -m "docs: explain concurrent translation logging"
```

- [ ] **Step 9: Run final clean verification**

Run:

```bash
.venv/bin/python -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, both Git checks produce no output, and no real LLM call is made.
