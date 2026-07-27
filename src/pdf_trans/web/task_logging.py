from __future__ import annotations

import logging
import sys

from pdf_trans.web.repository import TaskRepository


class TaskLogHandler(logging.Handler):
    def __init__(self, repository: TaskRepository) -> None:
        super().__init__(logging.INFO)
        self.repository = repository
        self._active_task_id: str | None = None

    def activate(self, task_id: str) -> None:
        with self.lock:
            if self._active_task_id is not None:
                raise RuntimeError("已有活动日志任务")
            self._active_task_id = task_id

    def deactivate(self) -> None:
        with self.lock:
            self._active_task_id = None

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("pdf_trans.web"):
            return
        with self.lock:
            task_id = self._active_task_id
            if task_id is None:
                return
            try:
                self.repository.append_log(
                    task_id, record.levelname, record.getMessage()
                )
            except Exception as exc:
                print(f"无法持久化任务日志：{exc}", file=sys.stderr)
