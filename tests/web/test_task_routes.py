from pdf_trans.web.storage import UploadValidationError


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
