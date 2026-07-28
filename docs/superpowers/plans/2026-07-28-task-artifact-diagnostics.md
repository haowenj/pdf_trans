# Task Artifact Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every CLI run a visible UUID and durable diagnostics, preserve MinerU responses, expose Web UUIDs, and add `cat_task <uuid> [--zip]` for listing and packaging CLI or Web task artifacts.

**Architecture:** CLI task lifecycle metadata lives in a small filesystem-backed `task_runs` module, while artifact discovery and ZIP creation live in a dependency-light `task_diagnostics` module. Existing workflow and Web components supply raw ZIPs and file logs; `__main__` dispatches the backward-compatible workflow syntax or the new `cat_task` command.

**Tech Stack:** Python 3.11+, standard-library `argparse`, `dataclasses`, `json`, `logging`, `pathlib`, `uuid`, and `zipfile`; pytest 8; existing FastAPI, SQLAlchemy, and Alembic Web extras.

## Global Constraints

- New CLI task directories are exactly `data/runs/cli-<uuid>/`; public task identifiers remain bare canonical UUID strings.
- Existing `data/runs/<uuid>/` CLI directories remain discoverable read-only.
- Existing `python -m pdf_trans <pdf>`, `--translate-only`, and `--svr-url` syntax remains compatible.
- Both full CLI runs and `--translate-only` runs create UUIDs, `task.json`, and `task.log`.
- Web keeps `data/web/tasks/<uuid>/attempts/<attempt-count>/...` and database-backed logs.
- Original PDFs are never listed or included in diagnostic ZIPs.
- All Web attempts are included; symlinks are never followed.
- Diagnostic ZIPs are named `pdf-trans-<uuid>.zip` in the current directory and never overwrite an existing file.
- No new runtime dependency is added to the base CLI.

---

### Task 1: CLI Task Lifecycle and File Logging

**Files:**
- Create: `src/pdf_trans/task_runs.py`
- Create: `tests/test_task_runs.py`
- Modify: `src/pdf_trans/logging_utils.py`
- Modify: `tests/test_logging_utils.py`
- Modify: `src/pdf_trans/__main__.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `CliTask`, `TaskManifestError`, `start_cli_task(runs_dir, task_type, input_path, *, task_id=None, now=None) -> CliTask`, `finish_cli_task(task, status, *, referenced_artifacts=(), error=None, now=None) -> None`, and `read_task_manifest(path) -> dict[str, object]`.
- Extends: `configure_logging(stream=None, *, log_path=None) -> None`.
- Consumers in later tasks: `task_diagnostics.collect_task_snapshot()` reads manifests through `read_task_manifest()`.

- [ ] **Step 1: Write failing task lifecycle tests**

Create `tests/test_task_runs.py` with focused tests:

```python
from datetime import datetime, timezone
import json
from uuid import UUID

import pytest

from pdf_trans.task_runs import (
    TaskManifestError,
    finish_cli_task,
    read_task_manifest,
    start_cli_task,
)


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 28, 10, 5, tzinfo=timezone.utc)
TASK_ID = "12345678-1234-4abc-8def-1234567890ab"


def test_start_and_finish_cli_task_writes_atomic_manifest(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")

    task = start_cli_task(
        tmp_path / "runs",
        "full",
        source,
        task_id=TASK_ID,
        now=NOW,
    )

    assert task.task_id == TASK_ID
    assert task.root == tmp_path / "runs" / f"cli-{TASK_ID}"
    assert task.log_path == task.root / "task.log"
    assert read_task_manifest(task.manifest_path)["status"] == "running"

    finish_cli_task(task, "succeeded", now=LATER)
    manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
    assert manifest["finished_at"] == LATER.isoformat()
    assert manifest["status"] == "succeeded"
    assert manifest["referenced_artifacts"] == []


def test_translate_only_manifest_records_only_explicit_references(tmp_path):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    translated = tmp_path / "translated_content_list.json"
    rendered = tmp_path / "rendered.md"
    task = start_cli_task(
        tmp_path / "runs",
        "translate-only",
        normalized,
        task_id=TASK_ID,
        now=NOW,
    )

    finish_cli_task(
        task,
        "failed",
        referenced_artifacts=(normalized, translated, rendered),
        error="translation failed",
        now=LATER,
    )

    manifest = read_task_manifest(task.manifest_path)
    assert manifest["referenced_artifacts"] == [
        str(normalized.resolve()),
        str(translated.resolve()),
        str(rendered.resolve()),
    ]
    assert manifest["error"] == "translation failed"


@pytest.mark.parametrize("task_type", ["other", "", 1])
def test_start_cli_task_rejects_invalid_task_type(tmp_path, task_type):
    with pytest.raises(TaskManifestError):
        start_cli_task(
            tmp_path / "runs",
            task_type,
            tmp_path / "input",
            task_id=TASK_ID,
            now=NOW,
        )


def test_generated_task_id_is_canonical_uuid(tmp_path):
    task = start_cli_task(tmp_path / "runs", "full", tmp_path / "paper.pdf")
    assert str(UUID(task.task_id)) == task.task_id


def test_read_task_manifest_rejects_corrupt_structure(tmp_path):
    path = tmp_path / "task.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(TaskManifestError, match="结构错误"):
        read_task_manifest(path)
```

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```bash
pytest tests/test_task_runs.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'pdf_trans.task_runs'`.

- [ ] **Step 3: Implement the filesystem task lifecycle**

Create `src/pdf_trans/task_runs.py` with these concrete types and rules:

```python
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from pdf_trans.errors import PDFTransError

TASK_TYPES = {"full", "translate-only"}
TASK_STATUSES = {"running", "succeeded", "failed"}


class TaskManifestError(PDFTransError):
    pass


@dataclass(frozen=True)
class CliTask:
    task_id: str
    root: Path
    manifest_path: Path
    log_path: Path


def _timestamp(value: datetime | None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def start_cli_task(
    runs_dir: Path,
    task_type: str,
    input_path: Path,
    *,
    task_id: str | None = None,
    now: datetime | None = None,
) -> CliTask:
    if task_type not in TASK_TYPES:
        raise TaskManifestError(f"未知 CLI 任务类型：{task_type}")
    identifier = str(uuid.uuid4()) if task_id is None else task_id
    try:
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError(identifier)
    except (ValueError, AttributeError) as exc:
        raise TaskManifestError(f"任务 UUID 格式错误：{identifier}") from exc
    root = runs_dir / f"cli-{identifier}"
    root.mkdir(parents=True, exist_ok=False)
    task = CliTask(identifier, root, root / "task.json", root / "task.log")
    _write_json_atomic(
        task.manifest_path,
        {
            "schema_version": 1,
            "task_id": identifier,
            "task_type": task_type,
            "status": "running",
            "created_at": _timestamp(now),
            "finished_at": None,
            "input_path": str(input_path.expanduser().resolve()),
            "task_root": str(root.resolve()),
            "referenced_artifacts": [],
            "error": None,
        },
    )
    return task


def read_task_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskManifestError(f"无法读取任务清单：{path}") from exc
    required = {
        "schema_version",
        "task_id",
        "task_type",
        "status",
        "created_at",
        "finished_at",
        "input_path",
        "task_root",
        "referenced_artifacts",
        "error",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise TaskManifestError(f"任务清单结构错误：{path}")
    if value["schema_version"] != 1:
        raise TaskManifestError(f"不支持的任务清单版本：{path}")
    if value["task_type"] not in TASK_TYPES:
        raise TaskManifestError(f"任务清单类型错误：{path}")
    if value["status"] not in TASK_STATUSES:
        raise TaskManifestError(f"任务清单状态错误：{path}")
    if not isinstance(value["referenced_artifacts"], list) or not all(
        isinstance(item, str) for item in value["referenced_artifacts"]
    ):
        raise TaskManifestError(f"任务清单产物引用错误：{path}")
    return value


def finish_cli_task(
    task: CliTask,
    status: str,
    *,
    referenced_artifacts: Sequence[Path] = (),
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    if status not in {"succeeded", "failed"}:
        raise TaskManifestError(f"非法结束状态：{status}")
    manifest = read_task_manifest(task.manifest_path)
    manifest.update(
        status=status,
        finished_at=_timestamp(now),
        referenced_artifacts=[
            str(path.expanduser().resolve()) for path in referenced_artifacts
        ],
        error=error[:2000] if error is not None else None,
    )
    _write_json_atomic(task.manifest_path, manifest)
```

The only deletion in this writer is its exact sibling `.task.json.tmp`;
never delete any broader directory.

- [ ] **Step 4: Add failing dual-output logging tests**

Add to `tests/test_logging_utils.py`:

```python
def test_configure_logging_writes_color_console_and_plain_file(tmp_path):
    console = io.StringIO()
    log_path = tmp_path / "task.log"
    configure_logging(console, log_path=log_path)

    logging.getLogger("pdf_trans.test").warning("需要排查")

    assert console.getvalue() == "\033[33m[WARN]\033[0m 需要排查\n"
    file_text = log_path.read_text(encoding="utf-8")
    assert "[WARNING] 需要排查" in file_text
    assert "\033[" not in file_text
```

Run:

```bash
pytest tests/test_logging_utils.py::test_configure_logging_writes_color_console_and_plain_file -v
```

Expected: FAIL because `configure_logging()` does not accept `log_path`.

- [ ] **Step 5: Extend logging configuration**

In `src/pdf_trans/logging_utils.py`, add `Path` and a plain formatter:

```python
from pathlib import Path


class PlainLogFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )


def configure_logging(
    stream: TextIO | None = None,
    *,
    log_path: Path | None = None,
) -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(ColorLevelFormatter())
    handlers: list[logging.Handler] = [console]
    if log_path is not None:
        file_handler = logging.FileHandler(
            log_path, mode="a", encoding="utf-8"
        )
        file_handler.setFormatter(PlainLogFormatter())
        handlers.append(file_handler)
    previous_handlers = package_logger.handlers[:]
    package_logger.handlers.clear()
    for previous in previous_handlers:
        previous.close()
    for handler in handlers:
        package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False
```

- [ ] **Step 6: Write failing CLI UUID, prefix, success, and failure tests**

Add these imports to `tests/test_cli.py`:

```python
import json
from types import SimpleNamespace
```

Update every existing run-directory assertion from parsing the whole directory
name as a UUID to:

```python
assert received["data_dir"].parent == cli.DEFAULT_DATA_DIR / "runs"
assert received["data_dir"].name.startswith("cli-")
UUID(received["data_dir"].name.removeprefix("cli-"))
```

In the two-invocation uniqueness test, use the same
`name.removeprefix("cli-")` validation for both directories. Then add:

```python
def test_full_cli_prints_uuid_uses_prefixed_root_and_finishes_manifest(
    tmp_path, monkeypatch, capsys
):
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: UUID(task_id))
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", tmp_path / "data")

    def fake_process(path, *, svr_url, data_dir):
        return SimpleNamespace(
            output_path=data_dir / "cleaned_content_list.json",
            normalized_path=data_dir / "normalized_content_list.json",
            normalized_count=0,
            markdown_path=data_dir / "rendered.md",
            candidates_path=data_dir / "cross_page_candidates.json",
            candidate_count=0,
            before_count=0,
            filtered_count=0,
            after_count=0,
            content_stats=SimpleNamespace(
                type_counts={},
                text_level_count=0,
                text_level_counts={},
                page_idx_counts={},
            ),
            translated_path=data_dir / "translated_content_list.json",
            translation_stats=TranslationStats(0, 0, 0, 0, 0, 0),
        )

    monkeypatch.setattr(cli, "process_pdf", fake_process)

    assert cli.main([str(pdf)]) == 0

    root = tmp_path / "data/runs" / f"cli-{task_id}"
    manifest = json.loads((root / "task.json").read_text(encoding="utf-8"))
    assert f"任务 UUID：{task_id}\n" in capsys.readouterr().out
    assert manifest["status"] == "succeeded"
    assert (root / "task.log").exists()


def test_translate_only_gets_uuid_and_records_external_artifacts(
    tmp_path, monkeypatch
):
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    translated = tmp_path / "translated_content_list.json"
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: UUID(task_id))
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(
        cli,
        "process_translation_file",
        lambda path: SimpleNamespace(
            translated_path=translated,
            stats=TranslationStats(0, 0, 0, 0, 0, 0),
        ),
    )
    monkeypatch.setattr(cli, "render_content_list_file", lambda *args: None)

    assert cli.main(["--translate-only", str(normalized)]) == 0

    root = tmp_path / "data/runs" / f"cli-{task_id}"
    manifest = json.loads((root / "task.json").read_text(encoding="utf-8"))
    assert manifest["task_type"] == "translate-only"
    assert manifest["referenced_artifacts"] == [
        str(normalized.resolve()),
        str(translated.resolve()),
        str((tmp_path / "rendered.md").resolve()),
    ]
```

Also add a failure test whose workflow raises `WorkflowError("boom")` and
assert `status == "failed"` and `error == "boom"`.

- [ ] **Step 7: Integrate the CLI task lifecycle**

In `src/pdf_trans/__main__.py`:

1. Generate the UUID once.
2. Call `start_cli_task(DEFAULT_DATA_DIR / "runs", task_type, input_path, task_id=str(uuid.uuid4()))`.
3. Print `任务 UUID：<uuid>` immediately.
4. Call `configure_logging(log_path=task.log_path)`.
5. Pass `task.root` as full-workflow `data_dir`.
6. On successful full workflow, finish with no external references.
7. On successful translate-only, finish with normalized, translated, and rendered paths.
8. On handled failure, finish as failed before logging and returning 1.
9. On an unexpected `BaseException`, best-effort finish as failed and re-raise.

Use a helper that never replaces an active exception:

```python
def _finish_task_safely(task, status, **kwargs) -> bool:
    try:
        finish_cli_task(task, status, **kwargs)
        return True
    except Exception as exc:
        LOGGER.error("无法更新任务清单：%s", exc)
        return False
```

If a successful workflow cannot update its final manifest, return 1. If a
workflow already failed, preserve its original exit behavior.

- [ ] **Step 8: Run Task 1 tests and commit**

Run:

```bash
pytest tests/test_task_runs.py tests/test_logging_utils.py tests/test_cli.py -v
```

Expected: all selected tests PASS.

Commit:

```bash
git add src/pdf_trans/task_runs.py src/pdf_trans/logging_utils.py \
  src/pdf_trans/__main__.py tests/test_task_runs.py \
  tests/test_logging_utils.py tests/test_cli.py
git commit -m "feat: record CLI task diagnostics"
```

---

### Task 2: Preserve the Raw MinerU ZIP

**Files:**
- Modify: `src/pdf_trans/archive.py`
- Modify: `src/pdf_trans/workflow.py`
- Modify: `tests/test_archive.py`
- Modify: `tests/test_workflow.py`

**Interfaces:**
- Extends: `extract_zip(archive_bytes, output_dir, *, reserved_paths=())`.
- Consumes: the existing `_process_pdf_stages(..., data_dir=...)` output root.
- Produces: `<data_dir>/mineru_result.zip` before extraction begins.
- No `WorkflowResult` field or public workflow signature changes.

- [ ] **Step 1: Write failing preservation tests**

Add to `tests/test_workflow.py`:

```python
def test_process_pdf_preserves_raw_mineru_zip(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    archive_bytes = make_result_zip([{"type": "text", "text": "正文"}])

    process_pdf(
        pdf,
        data_dir=tmp_path / "run",
        client=FakeMinerUClient(archive_bytes),
        translator=FakeTranslator(),
    )

    assert (tmp_path / "run/mineru_result.zip").read_bytes() == archive_bytes


def test_process_pdf_preserves_raw_zip_when_extraction_fails(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    invalid_zip = b"not a zip"

    with pytest.raises(ArchiveError):
        process_pdf(
            pdf,
            data_dir=tmp_path / "run",
            client=FakeMinerUClient(invalid_zip),
            translator=FakeTranslator(),
        )

    assert (tmp_path / "run/mineru_result.zip").read_bytes() == invalid_zip
```

Import `ArchiveError` from `pdf_trans.errors`.

- [ ] **Step 2: Run preservation tests and verify RED**

Run:

```bash
pytest tests/test_workflow.py::test_process_pdf_preserves_raw_mineru_zip \
  tests/test_workflow.py::test_process_pdf_preserves_raw_zip_when_extraction_fails -v
```

Expected: FAIL because `mineru_result.zip` does not exist.

- [ ] **Step 3: Write the reserved-path extraction test**

Add to `tests/test_archive.py`:

```python
def test_extract_zip_rejects_reserved_output_path(tmp_path):
    archive_bytes = make_zip({"mineru_result.zip": b"overwrite"})

    with pytest.raises(ArchiveError, match="保留路径"):
        extract_zip(
            archive_bytes,
            tmp_path / "data",
            reserved_paths=(PurePosixPath("mineru_result.zip"),),
        )
```

Import `PurePosixPath`.

- [ ] **Step 4: Run the reserved-path test and verify RED**

Run:

```bash
pytest tests/test_archive.py::test_extract_zip_rejects_reserved_output_path -v
```

Expected: FAIL because `extract_zip()` does not accept `reserved_paths`.

- [ ] **Step 5: Implement reserved-path extraction**

Extend `src/pdf_trans/archive.py`:

```python
def extract_zip(
    archive_bytes: bytes,
    output_dir: Path,
    *,
    reserved_paths: Iterable[PurePosixPath] = (),
) -> tuple[Path, ...]:
    reserved = frozenset(reserved_paths)
    # existing setup...
```

Immediately after constructing `member_path`, reject an exact reserved path:

```python
if member_path in reserved:
    raise ArchiveError(f"ZIP 包含保留路径：{member.filename}")
```

- [ ] **Step 6: Implement atomic raw ZIP storage**

In `src/pdf_trans/workflow.py`, import `os` and add:

```python
def _write_raw_archive(archive_bytes: bytes, output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    archive_path = output_root / "mineru_result.zip"
    temporary = output_root / ".mineru_result.zip.part"
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("xb") as handle:
            handle.write(archive_bytes)
        os.replace(temporary, archive_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return archive_path
```

Call it immediately after receiving `archive_bytes` and before entering
`extract_zip()`. Pass
`reserved_paths=(PurePosixPath("mineru_result.zip"),)` to extraction and import
`PurePosixPath` from `pathlib`. Update the extraction stage log to mention the
preserved raw archive path.

- [ ] **Step 7: Run archive and workflow tests and commit**

Run:

```bash
pytest tests/test_archive.py tests/test_workflow.py -v
```

Expected: all workflow tests PASS.

Commit:

```bash
git add src/pdf_trans/archive.py src/pdf_trans/workflow.py \
  tests/test_archive.py tests/test_workflow.py
git commit -m "feat: preserve MinerU result archives"
```

---

### Task 3: Mirror Web Task Logs to Files

**Files:**
- Modify: `src/pdf_trans/web/task_logging.py`
- Modify: `src/pdf_trans/web/app.py`
- Modify: `tests/web/test_task_logging.py`

**Interfaces:**
- Extends: `TaskLogHandler(repository, data_dir: Path | None = None)`.
- Produces: `data_dir / "tasks" / task_id / "task.log"` for active task records.
- Keeps: database logging and the current single-active-task lock semantics.

- [ ] **Step 1: Write failing mirrored-log tests**

Extend `tests/web/test_task_logging.py`:

```python
def test_handler_mirrors_database_logs_to_plain_task_file(
    tmp_path, repository
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    task_root = tmp_path / "tasks/a"
    task_root.mkdir(parents=True)
    handler = TaskLogHandler(repository, data_dir=tmp_path)
    record = logging.LogRecord(
        "pdf_trans.workflow",
        logging.WARNING,
        __file__,
        1,
        "阶段失败",
        (),
        None,
    )

    handler.activate("a")
    handler.emit(record)
    handler.deactivate()

    assert [log.message for log in repository.list_logs("a")] == ["阶段失败"]
    text = (task_root / "task.log").read_text(encoding="utf-8")
    assert "[WARNING] 阶段失败" in text
    assert "\033[" not in text


def test_handler_does_not_create_file_for_web_or_inactive_logs(
    tmp_path, repository
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    (tmp_path / "tasks/a").mkdir(parents=True)
    handler = TaskLogHandler(repository, data_dir=tmp_path)
    handler.activate("a")
    handler.emit(
        logging.LogRecord(
            "pdf_trans.web.routes", logging.INFO, __file__, 1, "request", (), None
        )
    )
    handler.deactivate()
    handler.emit(
        logging.LogRecord(
            "pdf_trans.workflow", logging.INFO, __file__, 1, "inactive", (), None
        )
    )

    assert not (tmp_path / "tasks/a/task.log").exists()
```

- [ ] **Step 2: Run mirrored-log tests and verify RED**

Run:

```bash
pytest tests/web/test_task_logging.py -v
```

Expected: FAIL because `TaskLogHandler` does not accept `data_dir`.

- [ ] **Step 3: Implement the file mirror**

In `src/pdf_trans/web/task_logging.py`:

```python
from pathlib import Path

from pdf_trans.logging_utils import PlainLogFormatter


class TaskLogHandler(logging.Handler):
    def __init__(
        self,
        repository: TaskRepository,
        data_dir: Path | None = None,
    ) -> None:
        super().__init__(logging.INFO)
        self.repository = repository
        self.data_dir = data_dir
        self.file_formatter = PlainLogFormatter()
        self._active_task_id: str | None = None
```

Within the existing lock, attempt the database append and file append as two
separate `try` blocks so one failed sink does not suppress the other. The file
sink appends `self.file_formatter.format(record) + "\n"` to
`data_dir / "tasks" / task_id / "task.log"` using UTF-8. Continue printing
sink errors to stderr without recursive logging.

In `src/pdf_trans/web/app.py`, construct:

```python
TaskLogHandler(repository, settings.data_dir)
```

- [ ] **Step 4: Run Web logging tests and commit**

Run:

```bash
pytest tests/web/test_task_logging.py tests/web/test_worker.py -v
```

Expected: all selected tests PASS.

Commit:

```bash
git add src/pdf_trans/web/task_logging.py src/pdf_trans/web/app.py \
  tests/web/test_task_logging.py
git commit -m "feat: mirror Web task logs to files"
```

---

### Task 4: Display and Copy Web Task UUIDs

**Files:**
- Modify: `src/pdf_trans/web/templates/dashboard.html`
- Modify: `src/pdf_trans/web/static/dashboard.js`
- Modify: `src/pdf_trans/web/static/app.css`
- Modify: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: existing `TaskView.id` and SSE `task.id`.
- Produces: `.task-id` showing the first eight UUID characters and a
  `data-action="copy-id"` button whose `data-task-id` source remains complete.

- [ ] **Step 1: Write failing UUID visibility tests**

Update `tests/web/test_pages.py`:

```python
def test_dashboard_displays_short_uuid_and_copy_action(
    web_client, repository
) -> None:
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    repository.create_task(
        task_id, "paper.pdf", f"tasks/{task_id}/upload/source.pdf"
    )

    response = web_client.get("/")
    script = web_client.get("/static/dashboard.js").text

    assert "12345678" in response.text
    assert 'data-action="copy-id"' in response.text
    assert "navigator.clipboard.writeText(row.dataset.taskId)" in script
    assert "task.id.slice(0, 8)" in script
```

- [ ] **Step 2: Run the UUID page test and verify RED**

Run:

```bash
pytest tests/web/test_pages.py::test_dashboard_displays_short_uuid_and_copy_action -v
```

Expected: FAIL because the server-rendered row lacks the copy action.

- [ ] **Step 3: Implement matching server and SSE markup**

In `dashboard.html`, under the task creation time:

```html
<small class="task-id" title="{{ task.id }}">
  UUID {{ task.id[:8] }}
  <button type="button" data-action="copy-id">复制</button>
</small>
```

In `dashboard.js`, build the same identity detail:

```javascript
const taskId = document.createElement('small');
taskId.className = 'task-id';
taskId.title = task.id;
taskId.append(`UUID ${task.id.slice(0, 8)} `);
taskId.append(actionButton('复制', 'copy-id'));
identity.append(name, detail, taskId);
```

Handle the click before Console/resume actions:

```javascript
if (action.dataset.action === 'copy-id') {
  navigator.clipboard.writeText(row.dataset.taskId);
} else if (action.dataset.action === 'console') {
```

In `app.css`, prevent the nested copy button from dominating the row:

```css
.task-id button {
  padding: 1px 6px;
  margin-left: 4px;
  font-size: 12px;
}
```

- [ ] **Step 4: Run page tests and commit**

Run:

```bash
pytest tests/web/test_pages.py -v
```

Expected: all page tests PASS.

Commit:

```bash
git add src/pdf_trans/web/templates/dashboard.html \
  src/pdf_trans/web/static/dashboard.js src/pdf_trans/web/static/app.css \
  tests/web/test_pages.py
git commit -m "feat: expose copyable Web task UUIDs"
```

---

### Task 5: Diagnostic Discovery and ZIP Engine

**Files:**
- Create: `src/pdf_trans/task_diagnostics.py`
- Create: `tests/test_task_diagnostics.py`

**Interfaces:**
- Consumes: CLI manifests from `read_task_manifest()`, CLI runs root, Web data
  root, and optional Web database URL.
- Produces: `DiagnosticEntry`, `TaskSnapshot`, `ZipResult`,
  `collect_task_snapshot(task_id, *, cli_runs_dir, web_data_dir, database_url=None)`,
  `format_task_snapshot(snapshot) -> str`, and
  `create_diagnostic_zip(snapshot, output_dir) -> ZipResult`.

- [ ] **Step 1: Write failing locator and collector tests**

Create `tests/test_task_diagnostics.py` with:

```python
from pathlib import Path
from zipfile import ZipFile

import pytest

from pdf_trans.task_diagnostics import (
    DiagnosticEntry,
    TaskDiagnosticError,
    TaskSnapshot,
    collect_task_snapshot,
    create_diagnostic_zip,
    format_task_snapshot,
)
from pdf_trans.task_runs import finish_cli_task, start_cli_task


TASK_ID = "12345678-1234-4abc-8def-1234567890ab"


def test_collects_prefixed_cli_files_sorted_without_symlinks(tmp_path):
    task = start_cli_task(
        tmp_path / "runs",
        "full",
        tmp_path / "paper.pdf",
        task_id=TASK_ID,
    )
    (task.root / "z.json").write_text("{}", encoding="utf-8")
    (task.root / "a.md").write_text("x", encoding="utf-8")
    (task.root / "outside-link").symlink_to(tmp_path / "outside")
    finish_cli_task(task, "succeeded")

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    assert snapshot.source == "cli"
    assert [entry.display_path for entry in snapshot.entries] == sorted(
        entry.display_path for entry in snapshot.entries
    )
    assert "outside-link" not in {
        entry.display_path for entry in snapshot.entries
    }


def test_collects_legacy_cli_directory_with_warning(tmp_path):
    legacy = tmp_path / "runs" / TASK_ID
    legacy.mkdir(parents=True)
    (legacy / "old.json").write_text("{}", encoding="utf-8")

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    assert snapshot.source == "cli-legacy"
    assert any("旧格式" in warning for warning in snapshot.warnings)


def test_running_cli_task_reports_snapshot_warning(tmp_path):
    start_cli_task(
        tmp_path / "runs",
        "full",
        tmp_path / "paper.pdf",
        task_id=TASK_ID,
    )

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    assert any("仍在运行" in warning for warning in snapshot.warnings)


def test_translate_only_collects_exact_references_and_marks_missing(tmp_path):
    normalized = tmp_path / "normalized_content_list.json"
    normalized.write_text("[]", encoding="utf-8")
    translated = tmp_path / "translated_content_list.json"
    translated.write_text("[]", encoding="utf-8")
    rendered = tmp_path / "rendered.md"
    task = start_cli_task(
        tmp_path / "runs",
        "translate-only",
        normalized,
        task_id=TASK_ID,
    )
    finish_cli_task(
        task,
        "failed",
        referenced_artifacts=(normalized, translated, rendered),
        error="render failed",
    )

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    referenced = {
        entry.display_path: entry
        for entry in snapshot.entries
        if entry.display_path.startswith("referenced/")
    }
    assert set(referenced) == {
        "referenced/normalized_content_list.json",
        "referenced/translated_content_list.json",
        "referenced/rendered.md",
    }
    assert referenced["referenced/rendered.md"].missing is True


def test_collects_all_web_attempts_and_excludes_upload(tmp_path):
    web_root = tmp_path / "web/tasks" / TASK_ID
    (web_root / "upload").mkdir(parents=True)
    (web_root / "upload/source.pdf").write_bytes(b"%PDF")
    for attempt in ("1", "2"):
        path = web_root / f"attempts/{attempt}/result.json"
        path.parent.mkdir(parents=True)
        path.write_text(attempt, encoding="utf-8")
    (web_root / "task.log").write_text("log", encoding="utf-8")

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
    )

    paths = {entry.display_path for entry in snapshot.entries}
    assert paths == {
        "attempts/1/result.json",
        "attempts/2/result.json",
        "task.log",
    }
```

Add explicit invalid, missing, and conflict tests:

```python
@pytest.mark.parametrize(
    "task_id",
    ["not-a-uuid", "12345678-1234-4ABC-8def-1234567890ab"],
)
def test_rejects_noncanonical_uuid_without_scanning(tmp_path, task_id):
    with pytest.raises(TaskDiagnosticError, match="UUID"):
        collect_task_snapshot(
            task_id,
            cli_runs_dir=tmp_path / "runs",
            web_data_dir=tmp_path / "web",
        )


def test_reports_checked_paths_when_task_is_missing(tmp_path):
    with pytest.raises(TaskDiagnosticError, match="未找到任务") as caught:
        collect_task_snapshot(
            TASK_ID,
            cli_runs_dir=tmp_path / "runs",
            web_data_dir=tmp_path / "web",
        )
    assert f"cli-{TASK_ID}" in str(caught.value)
    assert f"tasks/{TASK_ID}" in str(caught.value)


@pytest.mark.parametrize(
    ("first", "second"),
    [("new", "legacy"), ("new", "web"), ("legacy", "web")],
)
def test_rejects_multiple_matching_task_roots(
    tmp_path, first, second
):
    roots = {
        "new": tmp_path / "runs" / f"cli-{TASK_ID}",
        "legacy": tmp_path / "runs" / TASK_ID,
        "web": tmp_path / "web/tasks" / TASK_ID,
    }
    roots[first].mkdir(parents=True)
    roots[second].mkdir(parents=True)

    with pytest.raises(TaskDiagnosticError, match="定位冲突"):
        collect_task_snapshot(
            TASK_ID,
            cli_runs_dir=tmp_path / "runs",
            web_data_dir=tmp_path / "web",
        )
```

- [ ] **Step 2: Run locator tests and verify RED**

Run:

```bash
pytest tests/test_task_diagnostics.py -v
```

Expected: collection fails because `pdf_trans.task_diagnostics` does not exist.

- [ ] **Step 3: Implement diagnostic data types and filesystem collection**

Create `src/pdf_trans/task_diagnostics.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from pdf_trans.errors import PDFTransError
from pdf_trans.task_runs import read_task_manifest


class TaskDiagnosticError(PDFTransError):
    pass


@dataclass(frozen=True)
class DiagnosticEntry:
    display_path: str
    source_path: Path | None
    inline_bytes: bytes | None = None
    missing: bool = False

    @property
    def size(self) -> int | None:
        if self.missing:
            return None
        if self.inline_bytes is not None:
            return len(self.inline_bytes)
        if self.source_path is None:
            return None
        try:
            return self.source_path.stat().st_size
        except OSError:
            return None


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    source: str
    root: Path
    entries: tuple[DiagnosticEntry, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ZipResult:
    path: Path
    warnings: tuple[str, ...]


def _canonical_uuid(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except ValueError as exc:
        raise TaskDiagnosticError(f"任务 UUID 格式错误：{value}") from exc
    if parsed != value:
        raise TaskDiagnosticError(f"任务 UUID 必须使用标准小写格式：{value}")
    return parsed


def _regular_files(root: Path) -> list[DiagnosticEntry]:
    entries = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(DiagnosticEntry(relative, path))
    return entries


def collect_task_snapshot(
    task_id: str,
    *,
    cli_runs_dir: Path,
    web_data_dir: Path,
    database_url: str | None = None,
) -> TaskSnapshot:
    identifier = _canonical_uuid(task_id)
    candidates = (
        ("cli", cli_runs_dir / f"cli-{identifier}"),
        ("cli-legacy", cli_runs_dir / identifier),
        ("web", web_data_dir / "tasks" / identifier),
    )
    matches = [
        (source, root)
        for source, root in candidates
        if not root.is_symlink() and root.is_dir()
    ]
    if not matches:
        checked = "、".join(str(root) for _, root in candidates)
        raise TaskDiagnosticError(
            f"未找到任务 {identifier}；已检查：{checked}"
        )
    if len(matches) != 1:
        locations = "、".join(str(root) for _, root in matches)
        raise TaskDiagnosticError(
            f"任务定位冲突 {identifier}：{locations}"
        )

    source, root = matches[0]
    entries: list[DiagnosticEntry] = []
    warnings: list[str] = []
    if source == "cli-legacy":
        entries.extend(_regular_files(root))
        warnings.append(
            "旧格式 CLI 任务没有 task.json、task.log 和保留的 MinerU ZIP"
        )
    elif source == "cli":
        manifest = read_task_manifest(root / "task.json")
        if manifest["task_id"] != identifier:
            raise TaskDiagnosticError("任务清单 UUID 与目录不一致")
        entries.extend(_regular_files(root))
        if manifest["status"] == "running":
            warnings.append("任务仍在运行，当前内容只是变化中的快照")
        if manifest["task_type"] == "translate-only":
            allowed = {
                "normalized_content_list.json",
                "translated_content_list.json",
                "rendered.md",
            }
            seen: set[str] = set()
            for raw_path in manifest["referenced_artifacts"]:
                path = Path(raw_path)
                if path.name not in allowed or path.name in seen:
                    raise TaskDiagnosticError(
                        f"任务清单包含非法产物引用：{raw_path}"
                    )
                seen.add(path.name)
                available = (
                    not path.is_symlink() and path.is_file()
                )
                entries.append(
                    DiagnosticEntry(
                        f"referenced/{path.name}",
                        source_path=path if available else None,
                        missing=not available,
                    )
                )
                if not available:
                    warnings.append(f"引用文件缺失：{path}")
    else:
        attempts = root / "attempts"
        if not attempts.is_symlink() and attempts.is_dir():
            entries.extend(
                DiagnosticEntry(
                    f"attempts/{entry.display_path}",
                    entry.source_path,
                    entry.inline_bytes,
                    entry.missing,
                )
                for entry in _regular_files(attempts)
            )
        task_log = root / "task.log"
        has_file_log = not task_log.is_symlink() and task_log.is_file()
        if has_file_log:
            entries.append(DiagnosticEntry("task.log", task_log))

    entries.sort(key=lambda entry: entry.display_path)
    return TaskSnapshot(
        identifier,
        source,
        root.resolve(),
        tuple(entries),
        tuple(warnings),
    )
```

- [ ] **Step 4: Write failing legacy Web log tests**

Add a test that creates a Web task without `task.log`, monkeypatches
`pdf_trans.task_diagnostics._load_web_details`, and asserts an inline
`task.log` plus a running-task warning:

```python
def test_legacy_web_logs_become_virtual_task_log(tmp_path, monkeypatch):
    root = tmp_path / "web/tasks" / TASK_ID
    (root / "attempts/1").mkdir(parents=True)
    monkeypatch.setattr(
        "pdf_trans.task_diagnostics._load_web_details",
        lambda database_url, task_id: ("running", b"[INFO] old log\n"),
        raising=False,
    )

    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
        database_url="sqlite:///unused.db",
    )

    task_log = next(
        entry for entry in snapshot.entries if entry.display_path == "task.log"
    )
    assert task_log.inline_bytes == b"[INFO] old log\n"
    assert any("仍在运行" in warning for warning in snapshot.warnings)
```

Add the database-error test:

```python
def test_web_database_failure_keeps_filesystem_artifacts(
    tmp_path, monkeypatch
):
    root = tmp_path / "web/tasks" / TASK_ID
    result = root / "attempts/1/result.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")

    def fail(database_url, task_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "pdf_trans.task_diagnostics._load_web_details",
        fail,
        raising=False,
    )
    snapshot = collect_task_snapshot(
        TASK_ID,
        cli_runs_dir=tmp_path / "runs",
        web_data_dir=tmp_path / "web",
        database_url="sqlite:///missing.db",
    )

    assert [entry.display_path for entry in snapshot.entries] == [
        "attempts/1/result.json"
    ]
    assert any(
        "database unavailable" in warning
        for warning in snapshot.warnings
    )
```

- [ ] **Step 5: Implement lazy Web database details**

Implement `_load_web_details(database_url, task_id)` with imports inside the
function so base CLI startup does not import SQLAlchemy:

```python
def _load_web_details(
    database_url: str,
    task_id: str,
) -> tuple[str, bytes]:
    from pdf_trans.web.db import make_engine, make_session_factory
    from pdf_trans.web.repository import TaskRepository

    repository = TaskRepository(
        make_session_factory(make_engine(database_url))
    )
    task = repository.get_task(task_id)
    lines: list[str] = []
    cursor = 0
    while True:
        logs = repository.list_logs(task_id, after_id=cursor, limit=500)
        for log in logs:
            cursor = log.id
            lines.append(
                f"{log.created_at.isoformat()} [{log.level}] {log.message}\n"
            )
        if len(logs) < 500:
            break
    return task.status, "".join(lines).encode("utf-8")
```

For Web snapshots, call the loader whenever `database_url` is available to
obtain status. Use returned log bytes only when the filesystem `task.log` is
absent. Catch loader exceptions, append a warning, and retain filesystem
entries. For CLI, add a running warning from `task.json`.

Insert this block after the Web `task.log` filesystem check in
`collect_task_snapshot()`:

```python
if database_url is not None:
    try:
        status, log_bytes = _load_web_details(database_url, identifier)
        if status == "running":
            warnings.append(
                "任务仍在运行，当前内容只是变化中的快照"
            )
        if not has_file_log and log_bytes:
            entries.append(
                DiagnosticEntry(
                    "task.log",
                    source_path=None,
                    inline_bytes=log_bytes,
                )
            )
    except Exception as exc:
        warnings.append(f"无法读取 Web 数据库日志：{exc}")
elif not has_file_log:
    warnings.append("未配置 Web 数据库，无法导出旧任务日志")
```

- [ ] **Step 6: Write failing formatter and ZIP tests**

Add:

```python
def test_format_includes_source_root_sizes_missing_and_warnings(tmp_path):
    present = tmp_path / "task.log"
    present.write_bytes(b"x")
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="cli",
        root=tmp_path,
        entries=(
            DiagnosticEntry("task.log", source_path=present),
            DiagnosticEntry(
                "referenced/rendered.md",
                source_path=None,
                missing=True,
            ),
        ),
        warnings=("引用文件缺失",),
    )

    text = format_task_snapshot(snapshot)
    assert f"任务 UUID：{TASK_ID}" in text
    assert "任务来源：cli" in text
    assert "1 B  task.log" in text
    assert "[missing] referenced/rendered.md" in text
    assert "警告：" in text


def test_zip_preserves_layout_virtual_log_and_never_overwrites(tmp_path):
    result_file = tmp_path / "result.json"
    result_file.write_text("{}", encoding="utf-8")
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="web",
        root=tmp_path,
        entries=(
            DiagnosticEntry("attempts/1/result.json", result_file),
            DiagnosticEntry(
                "task.log",
                source_path=None,
                inline_bytes=b"[INFO] old log\n",
            ),
        ),
        warnings=(),
    )
    result = create_diagnostic_zip(snapshot, tmp_path)

    assert result.path == tmp_path / f"pdf-trans-{TASK_ID}.zip"
    with ZipFile(result.path) as archive:
        names = set(archive.namelist())
        prefix = f"pdf-trans-{TASK_ID}/"
        assert prefix + "attempts/1/result.json" in names
        assert archive.read(prefix + "task.log") == b"[INFO] old log\n"

    with pytest.raises(TaskDiagnosticError, match="已存在"):
        create_diagnostic_zip(snapshot, tmp_path)


def test_zip_rejects_parent_relative_member_and_removes_partial_file(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    snapshot = TaskSnapshot(
        task_id=TASK_ID,
        source="cli",
        root=tmp_path,
        entries=(DiagnosticEntry("../secret.txt", secret),),
        warnings=(),
    )

    with pytest.raises(TaskDiagnosticError, match="路径越界"):
        create_diagnostic_zip(snapshot, tmp_path)

    assert not (tmp_path / f"pdf-trans-{TASK_ID}.zip").exists()
```

- [ ] **Step 7: Implement formatting and exclusive ZIP creation**

Implement `format_task_snapshot()` with this exact shape:

```python
def format_task_snapshot(snapshot: TaskSnapshot) -> str:
    lines = [
        f"任务来源：{snapshot.source}",
        f"任务 UUID：{snapshot.task_id}",
        f"任务目录：{snapshot.root.resolve()}",
        "诊断文件：",
    ]
    for entry in sorted(snapshot.entries, key=lambda item: item.display_path):
        size = entry.size
        if entry.missing or size is None:
            lines.append(f"  [missing] {entry.display_path}")
        else:
            lines.append(f"  {size} B  {entry.display_path}")
    for warning in snapshot.warnings:
        lines.append(f"警告：{warning}")
    return "\n".join(lines) + "\n"
```

Implement ZIP creation with an exclusive outer file:

```python
def create_diagnostic_zip(
    snapshot: TaskSnapshot,
    output_dir: Path,
) -> ZipResult:
    from zipfile import ZIP_DEFLATED, ZipFile

    target = output_dir / f"pdf-trans-{snapshot.task_id}.zip"
    prefix = PurePosixPath(f"pdf-trans-{snapshot.task_id}")
    warnings: list[str] = []
    try:
        with target.open("xb") as raw:
            with ZipFile(raw, "w", compression=ZIP_DEFLATED) as archive:
                for entry in snapshot.entries:
                    if entry.missing:
                        continue
                    member = prefix / PurePosixPath(entry.display_path)
                    if member.is_absolute() or ".." in member.parts:
                        raise TaskDiagnosticError(
                            f"ZIP 条目路径越界：{entry.display_path}"
                        )
                    if entry.inline_bytes is not None:
                        archive.writestr(member.as_posix(), entry.inline_bytes)
                    elif (
                        entry.source_path is None
                        or entry.source_path.is_symlink()
                        or not entry.source_path.is_file()
                    ):
                        warnings.append(
                            f"打包时文件已缺失：{entry.display_path}"
                        )
                    else:
                        try:
                            archive.write(
                                entry.source_path,
                                arcname=member.as_posix(),
                            )
                        except FileNotFoundError:
                            warnings.append(
                                f"打包时文件已缺失：{entry.display_path}"
                            )
    except FileExistsError as exc:
        raise TaskDiagnosticError(f"诊断 ZIP 已存在：{target}") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return ZipResult(target.resolve(), tuple(warnings))
```

- [ ] **Step 8: Run diagnostics tests and commit**

Run:

```bash
pytest tests/test_task_diagnostics.py -v
```

Expected: all diagnostic collection, legacy log, formatting, security, and ZIP tests PASS.

Commit:

```bash
git add src/pdf_trans/task_diagnostics.py tests/test_task_diagnostics.py
git commit -m "feat: collect and package task diagnostics"
```

---

### Task 6: Add the `cat_task` Command and Documentation

**Files:**
- Modify: `src/pdf_trans/__main__.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `collect_task_snapshot()`, `format_task_snapshot()`,
  `create_diagnostic_zip()`, `WebSettings.from_env()`, and existing workflow
  CLI parsing.
- Produces:
  `python -m pdf_trans cat_task <uuid> [--zip]`.

- [ ] **Step 1: Write failing command dispatch tests**

Add to `tests/test_cli.py`:

```python
def test_cat_task_lists_snapshot_without_starting_cli_task(
    tmp_path, monkeypatch, capsys
):
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    snapshot = SimpleNamespace(
        task_id=task_id,
        source="cli",
        root=tmp_path,
        entries=(),
        warnings=(),
    )
    received = {}
    monkeypatch.setattr(
        cli, "collect_task_snapshot", lambda task_id, **kwargs: snapshot
    )
    monkeypatch.setattr(
        cli, "format_task_snapshot", lambda value: "diagnostic listing\n"
    )
    monkeypatch.setattr(
        cli.WebSettings,
        "from_env",
        lambda: SimpleNamespace(
            data_dir=tmp_path / "web",
            database_url="sqlite:///web.db",
        ),
    )
    monkeypatch.setattr(cli, "start_cli_task", lambda *args, **kwargs: received)

    assert cli.main(["cat_task", task_id]) == 0

    assert capsys.readouterr().out == "diagnostic listing\n"
    assert received == {}


def test_cat_task_zip_writes_current_directory(
    tmp_path, monkeypatch, capsys
):
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    monkeypatch.chdir(tmp_path)
    snapshot = SimpleNamespace(
        task_id=task_id,
        source="web",
        root=tmp_path / "web",
        entries=(),
        warnings=(),
    )
    monkeypatch.setattr(cli, "collect_task_snapshot", lambda *args, **kw: snapshot)
    monkeypatch.setattr(cli, "format_task_snapshot", lambda value: "files\n")
    monkeypatch.setattr(
        cli,
        "create_diagnostic_zip",
        lambda value, output_dir: SimpleNamespace(
            path=output_dir / f"pdf-trans-{task_id}.zip",
            warnings=(),
        ),
    )
    monkeypatch.setattr(
        cli.WebSettings,
        "from_env",
        lambda: SimpleNamespace(
            data_dir=tmp_path / "web",
            database_url="sqlite:///web.db",
        ),
    )

    assert cli.main(["cat_task", task_id, "--zip"]) == 0
    assert (
        f"诊断 ZIP：{tmp_path}/pdf-trans-{task_id}.zip"
        in capsys.readouterr().out
    )
```

Add an explicit failure-path command test:

```python
@pytest.mark.parametrize(
    ("task_id", "message"),
    [
        ("not-a-uuid", "任务 UUID 格式错误"),
        ("12345678-1234-4abc-8def-1234567890ab", "未找到任务"),
    ],
)
def test_cat_task_reports_diagnostic_errors(
    tmp_path, monkeypatch, capsys, task_id, message
):
    monkeypatch.setattr(
        cli.WebSettings,
        "from_env",
        lambda: SimpleNamespace(
            data_dir=tmp_path / "web",
            database_url="sqlite:///web.db",
        ),
    )

    def fail(*args, **kwargs):
        raise WorkflowError(message)

    monkeypatch.setattr(cli, "collect_task_snapshot", fail)

    assert cli.main(["cat_task", task_id]) == 1
    assert message in capsys.readouterr().err
```

- [ ] **Step 2: Run command tests and verify RED**

Run:

```bash
pytest tests/test_cli.py -k cat_task -v
```

Expected: FAIL because current parser treats `cat_task` as a PDF path.

- [ ] **Step 3: Implement backward-compatible command dispatch**

In `src/pdf_trans/__main__.py`, import `sys`, the diagnostic functions, and
`WebSettings`:

```python
import sys

from pdf_trans.task_diagnostics import (
    collect_task_snapshot,
    create_diagnostic_zip,
    format_task_snapshot,
)
from pdf_trans.web.config import WebSettings
```

Add:

```python
def build_cat_task_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pdf_trans cat_task",
        description="按 UUID 查看或打包 CLI/Web 任务诊断产物。",
    )
    parser.add_argument("task_uuid", help="任务的标准 UUID")
    parser.add_argument(
        "--zip",
        action="store_true",
        dest="create_zip",
        help="在当前目录创建诊断 ZIP",
    )
    return parser


def _cat_task_main(argv: Sequence[str]) -> int:
    args = build_cat_task_parser().parse_args(argv)
    try:
        settings = WebSettings.from_env()
        snapshot = collect_task_snapshot(
            args.task_uuid,
            cli_runs_dir=DEFAULT_DATA_DIR / "runs",
            web_data_dir=settings.data_dir,
            database_url=settings.database_url,
        )
        print(format_task_snapshot(snapshot), end="")
        if args.create_zip:
            result = create_diagnostic_zip(snapshot, Path.cwd())
            for warning in result.warnings:
                print(f"警告：{warning}")
            print(f"诊断 ZIP：{result.path}")
        return 0
    except (PDFTransError, OSError, ValueError) as exc:
        LOGGER.error("错误：%s", exc)
        return 1
```

At the top of `main()` normalize the argument list and dispatch without
creating a CLI task:

```python
arguments = list(sys.argv[1:] if argv is None else argv)
if arguments[:1] == ["cat_task"]:
    configure_logging()
    return _cat_task_main(arguments[1:])
```

Pass `arguments` to the existing workflow parser. Do not convert the whole
CLI to required subcommands.

- [ ] **Step 4: Run all CLI tests**

Run:

```bash
pytest tests/test_cli.py -v
```

Expected: existing workflow syntax and new `cat_task` tests all PASS.

- [ ] **Step 5: Document task UUIDs, logs, raw ZIPs, and diagnostics**

Update `README.md` with exact examples:

```bash
python -m pdf_trans paper.pdf
# 任务 UUID：12345678-1234-4abc-8def-1234567890ab

python -m pdf_trans cat_task 12345678-1234-4abc-8def-1234567890ab
python -m pdf_trans cat_task 12345678-1234-4abc-8def-1234567890ab --zip
```

Document:

- new CLI directories as `data/runs/cli-<uuid>/`;
- `task.json`, `task.log`, and `mineru_result.zip`;
- `--translate-only` receives its own task UUID while outputs stay beside the
  normalized input;
- Web UUID copy behavior and all-attempt diagnostics;
- original PDFs and symlinks are excluded;
- ZIP collision behavior;
- `PDF_TRANS_WEB_DATA_DIR` and `PDF_TRANS_DATABASE_URL` must match the Web
  deployment when inspecting Web tasks.

- [ ] **Step 6: Run fresh complete verification**

Run:

```bash
pytest -v
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m build
git diff --check
git status --short
```

Expected:

- complete test suite has zero failures;
- both sdist and wheel build successfully;
- `git diff --check` prints nothing;
- only `README.md`, `src/pdf_trans/__main__.py`, and `tests/test_cli.py` remain
  uncommitted from Task 6.

- [ ] **Step 7: Commit command integration**

```bash
git add src/pdf_trans/__main__.py tests/test_cli.py README.md
git commit -m "feat: add task diagnostic command"
```

- [ ] **Step 8: Verify final commit and clean status**

Run:

```bash
pytest -q
git status --short --branch
git log -6 --oneline
```

Expected: all tests pass, the working tree is clean, and the six task commits
are visible at the top of history.
