from __future__ import annotations

import logging
import threading

from pdf_trans.web.repository import TaskRepository
from pdf_trans.web.task_logging import TaskLogHandler
from pdf_trans.web.task_runner import TaskRunner

LOGGER = logging.getLogger("pdf_trans.task_worker")


class TaskWorker:
    def __init__(
        self,
        repository: TaskRepository,
        runner: TaskRunner,
        log_handler: TaskLogHandler,
        *,
        wait_seconds: float = 1.0,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.log_handler = log_handler
        self.wait_seconds = wait_seconds
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="pdf-trans-web-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.notify()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def notify(self) -> None:
        with self._condition:
            self._condition.notify()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            task = self.repository.claim_next_task()
            if task is None:
                with self._condition:
                    self._condition.wait(timeout=self.wait_seconds)
                continue

            self.log_handler.activate(task.id)
            LOGGER.info(
                "开始第 %d 次执行：%s",
                task.attempt_count,
                task.original_filename,
            )
            try:
                artifacts = self.runner.run(task)
                LOGGER.info("%s", artifacts.summary)
                self.repository.mark_succeeded(
                    task.id,
                    normalized_path=artifacts.normalized_path,
                    markdown_path=artifacts.markdown_path,
                )
            except Exception as exc:
                LOGGER.exception("任务执行失败：%s", exc)
                self.repository.mark_failed(task.id, str(exc))
            finally:
                self.log_handler.deactivate()
