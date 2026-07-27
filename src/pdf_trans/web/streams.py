from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial

from anyio import to_thread

from pdf_trans.web.repository import LogView, TaskRepository, TaskView

POLL_SECONDS = 0.5
KEEPALIVE_SECONDS = 15.0


def task_to_dict(task: TaskView) -> dict[str, object]:
    return {
        "id": task.id,
        "original_filename": task.original_filename,
        "status": task.status,
        "attempt_count": task.attempt_count,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "started_at": (
            task.started_at.isoformat() if task.started_at else None
        ),
        "finished_at": (
            task.finished_at.isoformat() if task.finished_at else None
        ),
    }


def log_to_dict(log: LogView) -> dict[str, object]:
    return {
        "id": log.id,
        "task_id": log.task_id,
        "level": log.level,
        "message": log.message,
        "created_at": log.created_at.isoformat(),
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


async def task_event_stream(
    repository: TaskRepository,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    previous = ""
    idle = 0.0
    while not await is_disconnected():
        tasks = await to_thread.run_sync(repository.list_tasks)
        payload = _json([task_to_dict(task) for task in tasks])
        if payload != previous:
            previous = payload
            idle = 0.0
            yield f"event: tasks\ndata: {payload}\n\n"
            continue
        await sleep(POLL_SECONDS)
        idle += POLL_SECONDS
        if idle >= KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"


async def log_event_stream(
    repository: TaskRepository,
    *,
    task_id: str,
    after_id: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    cursor = after_id
    idle = 0.0
    while not await is_disconnected():
        logs = await to_thread.run_sync(
            partial(
                repository.list_logs,
                task_id,
                after_id=cursor,
                limit=500,
            )
        )
        if logs:
            idle = 0.0
            for log in logs:
                cursor = log.id
                yield (
                    f"id: {log.id}\n"
                    "event: log\n"
                    f"data: {_json(log_to_dict(log))}\n\n"
                )
            continue
        await sleep(POLL_SECONDS)
        idle += POLL_SECONDS
        if idle >= KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"
