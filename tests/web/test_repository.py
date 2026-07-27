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
