import pytest

from pdf_trans.web.repository import TaskNotFound
from pdf_trans.web.storage import StorageError, UploadValidationError


def test_upload_returns_202_and_notifies_worker(web_client, repository) -> None:
    response = web_client.post(
        "/tasks",
        files={"pdf": ("paper.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert repository.get_task(payload["id"]).original_filename == "paper.pdf"
    assert web_client.app.state.worker.notify_count == 1


def test_resume_requires_failed_or_interrupted_state(
    web_client, repository
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    assert web_client.post("/tasks/a/resume").status_code == 409
    repository.claim_next_task()
    repository.mark_failed("a", "timeout")
    response = web_client.post("/tasks/a/resume")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_upload_maps_validation_errors(web_client, monkeypatch) -> None:
    invalid = web_client.post(
        "/tasks",
        files={"pdf": ("paper.txt", b"no", "text/plain")},
    )
    assert invalid.status_code == 400

    async def too_large(*args, **kwargs):
        raise UploadValidationError("PDF 超过上传大小限制")

    monkeypatch.setattr(
        "pdf_trans.web.routes.tasks.save_pdf_upload",
        too_large,
    )
    oversized = web_client.post(
        "/tasks",
        files={"pdf": ("paper.pdf", b"%PDF-x", "application/pdf")},
    )
    assert oversized.status_code == 413


def test_missing_task_routes_return_404(web_client) -> None:
    assert web_client.post("/tasks/missing/resume").status_code == 404
    assert web_client.get("/tasks/missing/logs").status_code == 404


def test_log_history_uses_after_id(web_client, repository) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    first = repository.append_log("a", "INFO", "first")
    repository.append_log("a", "INFO", "second")

    response = web_client.get(f"/tasks/a/logs?after_id={first.id}")

    assert response.status_code == 200
    assert [item["message"] for item in response.json()] == ["second"]


def _create_failed_task(repository, task_id: str) -> None:
    repository.create_task(
        task_id,
        f"{task_id}.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )
    repository.claim_next_task()
    repository.mark_failed(task_id, "failed")


def test_delete_task_removes_database_logs_and_web_files(
    web_client,
    repository,
) -> None:
    task_id = "12345678-1234-4abc-8def-1234567890ab"
    _create_failed_task(repository, task_id)
    repository.append_log(task_id, "INFO", "delete me")
    data_dir = web_client.app.state.settings.data_dir
    task_root = data_dir / "tasks" / task_id
    source = task_root / "upload/source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.7")
    cli_manifest = data_dir / "runs/cli-keep/task.json"
    cli_manifest.parent.mkdir(parents=True)
    cli_manifest.write_text("{}", encoding="utf-8")

    response = web_client.delete(f"/tasks/{task_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert not task_root.exists()
    assert cli_manifest.read_text(encoding="utf-8") == "{}"
    with pytest.raises(TaskNotFound):
        repository.get_task(task_id)
    assert web_client.get(f"/tasks/{task_id}/logs").status_code == 404
    assert web_client.post(f"/tasks/{task_id}/resume").status_code == 404
    assert web_client.get(f"/tasks/{task_id}/view").status_code == 404
    assert web_client.get(f"/tasks/{task_id}/markdown").status_code == 404
    repository.create_task(
        task_id,
        "replacement.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )
    assert repository.list_logs(task_id) == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_delete_task_rejects_active_states(
    web_client,
    repository,
    status,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    if status == "running":
        repository.claim_next_task()
    task_root = web_client.app.state.settings.data_dir / "tasks/a"
    task_root.mkdir(parents=True)

    response = web_client.delete("/tasks/a")

    assert response.status_code == 409
    assert response.json()["detail"] == "当前任务状态不能删除"
    assert repository.get_task("a").status == status
    assert task_root.exists()


def test_delete_task_reports_missing_task(web_client) -> None:
    response = web_client.delete("/tasks/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在"


def test_delete_task_preserves_database_when_storage_fails(
    web_client,
    repository,
    monkeypatch,
) -> None:
    _create_failed_task(repository, "a")
    repository.append_log("a", "INFO", "preserve me")

    def fail_delete(data_dir, task_id):
        raise StorageError("无法删除任务文件")

    monkeypatch.setattr(
        "pdf_trans.web.routes.tasks.delete_task_directory",
        fail_delete,
    )

    response = web_client.delete("/tasks/a")

    assert response.status_code == 500
    assert response.json()["detail"] == "无法删除任务文件"
    assert repository.get_task("a").status == "failed"
    assert [log.message for log in repository.list_logs("a")] == [
        "preserve me"
    ]
