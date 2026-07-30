from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from pdf_trans.web.models import Task, TaskLog, utc_now

RECOVERABLE_STATES = {"failed", "interrupted"}
DELETABLE_STATES = RECOVERABLE_STATES | {"succeeded"}
RECENT_LOG_LIMIT = 200


class TaskNotFound(LookupError):
    pass


class InvalidTaskState(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskView:
    id: str
    original_filename: str
    status: str
    attempt_count: int
    source_pdf_path: str
    normalized_path: str | None
    markdown_path: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class LogView:
    id: int
    task_id: str
    level: str
    message: str
    created_at: datetime


def _task_view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        original_filename=task.original_filename,
        status=task.status,
        attempt_count=task.attempt_count,
        source_pdf_path=task.source_pdf_path,
        normalized_path=task.normalized_path,
        markdown_path=task.markdown_path,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def _log_view(log: TaskLog) -> LogView:
    return LogView(
        id=log.id,
        task_id=log.task_id,
        level=log.level,
        message=log.message,
        created_at=log.created_at,
    )


class TaskRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_task(
        self,
        task_id: str,
        original_filename: str,
        source_pdf_path: str,
    ) -> TaskView:
        with self._sessions.begin() as session:
            task = Task(
                id=task_id,
                original_filename=original_filename,
                status="queued",
                source_pdf_path=source_pdf_path,
            )
            session.add(task)
            session.flush()
            return _task_view(task)

    def get_task(self, task_id: str) -> TaskView:
        with self._sessions() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            return _task_view(task)

    def list_tasks(self, limit: int = 100) -> list[TaskView]:
        statement: Select[tuple[Task]] = (
            select(Task)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(limit)
        )
        with self._sessions() as session:
            return [_task_view(task) for task in session.scalars(statement)]

    def claim_next_task(self) -> TaskView | None:
        with self._sessions.begin() as session:
            task_id = session.scalar(
                select(Task.id)
                .where(Task.status == "queued")
                .order_by(Task.created_at, Task.id)
                .limit(1)
            )
            if task_id is None:
                return None

            now = utc_now()
            result = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == "queued")
                .values(
                    status="running",
                    attempt_count=Task.attempt_count + 1,
                    started_at=now,
                    finished_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
            task = session.get(Task, task_id)
            return _task_view(task)

    def interrupt_running(self) -> int:
        now = utc_now()
        with self._sessions.begin() as session:
            result = session.execute(
                update(Task)
                .where(Task.status == "running")
                .values(
                    status="interrupted",
                    error_message="服务在任务完成前停止，可继续执行",
                    finished_at=now,
                    updated_at=now,
                )
            )
            return int(result.rowcount)

    def resume_task(self, task_id: str) -> TaskView:
        now = utc_now()
        with self._sessions.begin() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            if task.status not in RECOVERABLE_STATES:
                raise InvalidTaskState(task.status)
            task.status = "queued"
            task.error_message = None
            task.finished_at = None
            task.updated_at = now
            session.flush()
            return _task_view(task)

    def mark_succeeded(
        self,
        task_id: str,
        *,
        normalized_path: str,
        markdown_path: str,
    ) -> TaskView:
        return self._finish(
            task_id,
            status="succeeded",
            normalized_path=normalized_path,
            markdown_path=markdown_path,
            error_message=None,
        )

    def mark_failed(self, task_id: str, error_message: str) -> TaskView:
        return self._finish(
            task_id,
            status="failed",
            error_message=error_message[:2000],
        )

    def _finish(self, task_id: str, *, status: str, **values) -> TaskView:
        now = utc_now()
        with self._sessions.begin() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            for name, value in values.items():
                setattr(task, name, value)
            task.status = status
            task.finished_at = now
            task.updated_at = now
            session.flush()
            return _task_view(task)

    def delete_task(
        self,
        task_id: str,
        *,
        cleanup: Callable[[], None],
    ) -> None:
        with self._sessions.begin() as session:
            task = session.scalar(
                select(Task)
                .where(Task.id == task_id)
                .with_for_update()
            )
            if task is None:
                raise TaskNotFound(task_id)
            if task.status not in DELETABLE_STATES:
                raise InvalidTaskState(task.status)

            session.execute(delete(TaskLog).where(TaskLog.task_id == task_id))
            session.delete(task)
            session.flush()
            cleanup()

    def append_log(self, task_id: str, level: str, message: str) -> LogView:
        with self._sessions.begin() as session:
            if session.get(Task, task_id) is None:
                raise TaskNotFound(task_id)
            log = TaskLog(
                task_id=task_id,
                level=level[:16],
                message=message,
            )
            session.add(log)
            session.flush()
            return _log_view(log)

    def list_logs(
        self,
        task_id: str,
        *,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[LogView]:
        self.get_task(task_id)
        statement = (
            select(TaskLog)
            .where(TaskLog.task_id == task_id, TaskLog.id > after_id)
            .order_by(TaskLog.id)
            .limit(limit)
        )
        with self._sessions() as session:
            return [_log_view(log) for log in session.scalars(statement)]

    def list_recent_logs(self, task_id: str) -> list[LogView]:
        self.get_task(task_id)
        statement = (
            select(TaskLog)
            .where(TaskLog.task_id == task_id)
            .order_by(TaskLog.id.desc())
            .limit(RECENT_LOG_LIMIT)
        )
        with self._sessions() as session:
            logs = [_log_view(log) for log in session.scalars(statement)]
        logs.reverse()
        return logs
