from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from pdf_trans.web.repository import TaskNotFound, TaskRepository
from pdf_trans.web.streams import log_event_stream, log_to_dict

router = APIRouter()
DOWNLOAD_PAGE_SIZE = 500


def iter_log_lines(
    repository: TaskRepository,
    task_id: str,
) -> Iterator[str]:
    cursor = 0
    while True:
        logs = repository.list_logs(
            task_id,
            after_id=cursor,
            limit=DOWNLOAD_PAGE_SIZE,
        )
        if not logs:
            return
        for log in logs:
            cursor = log.id
            yield f"[{log.level}] {log.message}\n"


@router.get("/tasks/{task_id}/logs")
def task_logs(
    request: Request,
    task_id: str,
    after_id: int = Query(0, ge=0),
) -> list[dict[str, object]]:
    try:
        logs = request.app.state.repository.list_logs(
            task_id, after_id=after_id, limit=500
        )
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    return [log_to_dict(log) for log in logs]


@router.get("/tasks/{task_id}/logs/recent")
def recent_task_logs(
    request: Request,
    task_id: str,
) -> list[dict[str, object]]:
    try:
        logs = request.app.state.repository.list_recent_logs(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    return [log_to_dict(log) for log in logs]


@router.get("/tasks/{task_id}/logs/download")
def download_task_logs(
    request: Request,
    task_id: str,
) -> StreamingResponse:
    repository = request.app.state.repository
    try:
        repository.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    return StreamingResponse(
        iter_log_lines(repository, task_id),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="task-{task_id}.log"'
            )
        },
    )


@router.get("/tasks/{task_id}/logs/events")
async def task_log_events(
    request: Request,
    task_id: str,
    after_id: int = Query(0, ge=0),
) -> StreamingResponse:
    try:
        request.app.state.repository.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    raw_cursor = request.headers.get("last-event-id")
    if raw_cursor is not None:
        try:
            after_id = max(0, int(raw_cursor))
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID 必须是整数") from exc
    generator = log_event_stream(
        request.app.state.repository,
        task_id=task_id,
        after_id=after_id,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
