import pytest

from pdf_trans.web.repository import InvalidTaskState, TaskNotFound


def test_repository_claims_oldest_task_and_increments_attempt(
    repository,
) -> None:
    second = repository.create_task("b", "b.pdf", "tasks/b/upload/source.pdf")
    first = repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")

    claimed = repository.claim_next_task()

    assert claimed is not None
    assert claimed.id == second.id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert repository.get_task(first.id).status == "queued"


def test_repository_interrupts_and_resumes_only_recoverable_states(
    repository,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    repository.claim_next_task()

    assert repository.interrupt_running() == 1
    assert repository.get_task("a").status == "interrupted"
    resumed = repository.resume_task("a")
    assert resumed.status == "queued"
    assert resumed.error_message is None

    with pytest.raises(InvalidTaskState):
        repository.resume_task("a")
    with pytest.raises(TaskNotFound):
        repository.resume_task("missing")


def test_repository_persists_ordered_logs_and_success_artifacts(
    repository,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    repository.claim_next_task()
    first = repository.append_log("a", "INFO", "开始")
    second = repository.append_log("a", "WARN", "重试")
    repository.mark_succeeded(
        "a",
        normalized_path="tasks/a/attempts/1/paper/normalized_content_list.json",
        markdown_path="tasks/a/attempts/1/paper/rendered.md",
    )

    assert [log.id for log in repository.list_logs("a")] == [
        first.id,
        second.id,
    ]
    assert [
        log.message for log in repository.list_logs("a", after_id=first.id)
    ] == ["重试"]
    task = repository.get_task("a")
    assert task.status == "succeeded"
    assert task.finished_at is not None


def _finish_task(repository, task_id: str, status: str) -> None:
    repository.create_task(
        task_id,
        f"{task_id}.pdf",
        f"tasks/{task_id}/upload/source.pdf",
    )
    repository.claim_next_task()
    if status == "succeeded":
        repository.mark_succeeded(
            task_id,
            normalized_path=f"tasks/{task_id}/normalized.json",
            markdown_path=f"tasks/{task_id}/rendered.md",
        )
    elif status == "failed":
        repository.mark_failed(task_id, "failed")
    else:
        assert status == "interrupted"
        assert repository.interrupt_running() == 1


@pytest.mark.parametrize(
    "status",
    ["succeeded", "failed", "interrupted"],
)
def test_repository_deletes_terminal_task_and_logs(
    repository,
    status,
) -> None:
    _finish_task(repository, "a", status)
    repository.append_log("a", "INFO", "before delete")
    cleanup_calls = []

    repository.delete_task(
        "a",
        cleanup=lambda: cleanup_calls.append("called"),
    )

    assert cleanup_calls == ["called"]
    with pytest.raises(TaskNotFound):
        repository.get_task("a")
    repository.create_task("a", "new.pdf", "tasks/a/upload/source.pdf")
    assert repository.list_logs("a") == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_repository_rejects_deleting_active_task(
    repository,
    status,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    if status == "running":
        repository.claim_next_task()
    cleanup_calls = []

    with pytest.raises(InvalidTaskState):
        repository.delete_task(
            "a",
            cleanup=lambda: cleanup_calls.append("called"),
        )

    assert cleanup_calls == []
    assert repository.get_task("a").status == status


def test_repository_delete_reports_missing_task(repository) -> None:
    with pytest.raises(TaskNotFound):
        repository.delete_task("missing", cleanup=lambda: None)


def test_repository_rolls_back_when_delete_cleanup_fails(repository) -> None:
    _finish_task(repository, "a", "failed")
    repository.append_log("a", "INFO", "preserve me")

    def fail_cleanup() -> None:
        raise OSError("locked")

    with pytest.raises(OSError, match="locked"):
        repository.delete_task("a", cleanup=fail_cleanup)

    assert repository.get_task("a").status == "failed"
    assert [log.message for log in repository.list_logs("a")] == [
        "preserve me"
    ]
