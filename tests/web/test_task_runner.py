from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from pdf_trans.web.config import WebSettings
from pdf_trans.web.repository import TaskView
from pdf_trans.web.task_runner import (
    AmbiguousCheckpoint,
    TaskRunner,
    WorkflowServices,
)


def task_view(*, attempt_count: int = 1) -> TaskView:
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    return TaskView(
        id="a",
        original_filename="paper.pdf",
        status="running",
        attempt_count=attempt_count,
        source_pdf_path="tasks/a/upload/source.pdf",
        normalized_path=None,
        markdown_path=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def settings(tmp_path) -> WebSettings:
    return WebSettings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'db'}",
        max_upload_mib=200,
        mineru_url="http://mineru:7100",
        host="127.0.0.1",
        port=8000,
    )


def test_runner_starts_full_workflow_when_no_checkpoint_exists(tmp_path):
    source = tmp_path / "tasks/a/upload/source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-")
    received: dict[str, object] = {}

    def full(path, *, svr_url, data_dir):
        received.update(path=path, svr_url=svr_url, data_dir=data_dir)
        normalized = data_dir / "paper/normalized_content_list.json"
        markdown = data_dir / "paper/rendered.md"
        normalized.parent.mkdir(parents=True)
        normalized.write_text("[]", encoding="utf-8")
        markdown.write_text("# 译文", encoding="utf-8")
        return SimpleNamespace(
            normalized_path=normalized,
            markdown_path=markdown,
            before_count=1,
            filtered_count=0,
            candidate_count=0,
            translation_stats=SimpleNamespace(
                success_count=1, failed_count=0
            ),
        )

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(full, lambda path: None, lambda source, output: None),
    )
    result = runner.run(task_view())

    assert received["data_dir"] == tmp_path / "tasks/a/attempts/1"
    assert result.normalized_path.endswith("normalized_content_list.json")
    assert result.markdown_path.endswith("rendered.md")


def test_runner_resumes_translation_and_renders_markdown(tmp_path):
    normalized = (
        tmp_path
        / "tasks/a/attempts/1/paper/normalized_content_list.json"
    )
    normalized.parent.mkdir(parents=True)
    normalized.write_text("[]", encoding="utf-8")
    translated = normalized.with_name("translated_content_list.json")
    translated.write_text("[]", encoding="utf-8")
    calls: list[tuple[object, ...]] = []

    def translate(path):
        calls.append(("translate", path))
        return SimpleNamespace(
            normalized_path=path,
            translated_path=translated,
            stats=SimpleNamespace(success_count=3, failed_count=0),
        )

    def render(source, output):
        calls.append(("render", source, output))
        output.write_text("# resumed", encoding="utf-8")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(lambda *args, **kwargs: None, translate, render),
    )
    result = runner.run(task_view(attempt_count=2))

    assert calls[0] == ("translate", normalized)
    assert calls[1][2] == normalized.with_name("rendered.md")
    assert result.resumed is True


def test_runner_rejects_multiple_normalized_checkpoints(tmp_path):
    for attempt in ("1", "2"):
        path = (
            tmp_path
            / f"tasks/a/attempts/{attempt}/paper/normalized_content_list.json"
        )
        path.parent.mkdir(parents=True)
        path.write_text("[]", encoding="utf-8")
    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(None, None, None),
    )

    with pytest.raises(AmbiguousCheckpoint):
        runner.run(task_view(attempt_count=3))
