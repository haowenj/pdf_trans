import logging
import threading
import time
from types import SimpleNamespace

from pdf_trans.web.task_logging import TaskLogHandler
from pdf_trans.web.worker import TaskWorker


def test_worker_executes_queued_tasks_serially(repository) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    repository.create_task("b", "b.pdf", "tasks/b/upload/source.pdf")
    active = 0
    maximum = 0
    completed = threading.Event()

    class Runner:
        def run(self, task):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            time.sleep(0.01)
            active -= 1
            if task.id == "b":
                completed.set()
            return SimpleNamespace(
                normalized_path=f"tasks/{task.id}/normalized.json",
                markdown_path=f"tasks/{task.id}/rendered.md",
                summary="完成",
            )

    worker = TaskWorker(
        repository, Runner(), TaskLogHandler(repository), wait_seconds=0.01
    )
    worker.start()
    try:
        worker.notify()
        assert completed.wait(1)
    finally:
        worker.stop()

    assert maximum == 1
    assert repository.get_task("a").status == "succeeded"
    assert repository.get_task("b").status == "succeeded"


def test_worker_persists_failure_and_error_log(repository) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")

    class FailingRunner:
        def run(self, task):
            raise RuntimeError("boom")

    worker = TaskWorker(
        repository,
        FailingRunner(),
        TaskLogHandler(repository),
        wait_seconds=0.01,
    )
    package_logger = logging.getLogger("pdf_trans")
    package_logger.addHandler(worker.log_handler)
    package_logger.setLevel(logging.INFO)
    worker.start()
    try:
        worker.notify()
        deadline = time.monotonic() + 1
        while (
            repository.get_task("a").status != "failed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
    finally:
        worker.stop()
        package_logger.removeHandler(worker.log_handler)

    task = repository.get_task("a")
    assert task.status == "failed"
    assert task.error_message == "boom"
    assert any(
        log.level == "ERROR" and "boom" in log.message
        for log in repository.list_logs("a")
    )
