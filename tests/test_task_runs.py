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
