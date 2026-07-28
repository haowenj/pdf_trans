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


def _canonical_task_id(value: str) -> str:
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TaskManifestError(f"任务 UUID 格式错误：{value}") from exc
    if parsed != value:
        raise TaskManifestError(f"任务 UUID 必须使用标准小写格式：{value}")
    return parsed


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


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
    identifier = _canonical_task_id(
        str(uuid.uuid4()) if task_id is None else task_id
    )
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
    if not isinstance(value["task_id"], str):
        raise TaskManifestError(f"任务清单 UUID 错误：{path}")
    _canonical_task_id(value["task_id"])
    if value["task_type"] not in TASK_TYPES:
        raise TaskManifestError(f"任务清单类型错误：{path}")
    if value["status"] not in TASK_STATUSES:
        raise TaskManifestError(f"任务清单状态错误：{path}")
    if not isinstance(value["created_at"], str):
        raise TaskManifestError(f"任务清单创建时间错误：{path}")
    if not (
        value["finished_at"] is None
        or isinstance(value["finished_at"], str)
    ):
        raise TaskManifestError(f"任务清单结束时间错误：{path}")
    if not isinstance(value["input_path"], str) or not isinstance(
        value["task_root"], str
    ):
        raise TaskManifestError(f"任务清单路径错误：{path}")
    references = value["referenced_artifacts"]
    if not isinstance(references, list) or not all(
        isinstance(item, str) for item in references
    ):
        raise TaskManifestError(f"任务清单产物引用错误：{path}")
    if not (value["error"] is None or isinstance(value["error"], str)):
        raise TaskManifestError(f"任务清单错误摘要格式错误：{path}")
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
