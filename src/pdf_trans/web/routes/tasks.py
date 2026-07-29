from __future__ import annotations

import shutil
import uuid

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse

from pdf_trans.web.repository import InvalidTaskState, TaskNotFound
from pdf_trans.web.storage import (
    StorageError,
    UploadValidationError,
    delete_task_directory,
    save_pdf_upload,
)
from pdf_trans.web.streams import task_event_stream, task_to_dict

router = APIRouter()


@router.post("/tasks")
async def upload_task(
    request: Request,
    pdf: UploadFile = File(...),
) -> JSONResponse:
    task_id = str(uuid.uuid4())
    settings = request.app.state.settings
    try:
        stored = await save_pdf_upload(
            pdf,
            task_id=task_id,
            data_dir=settings.data_dir,
            max_bytes=settings.max_upload_bytes,
        )
    except UploadValidationError as exc:
        status = 413 if "大小" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    try:
        task = request.app.state.repository.create_task(
            task_id,
            stored.original_filename,
            stored.relative_path,
        )
    except Exception:
        shutil.rmtree(
            settings.data_dir / "tasks" / task_id,
            ignore_errors=True,
        )
        raise
    request.app.state.worker.notify()
    return JSONResponse(task_to_dict(task), status_code=202)


@router.post("/tasks/{task_id}/resume")
def resume_task(request: Request, task_id: str) -> JSONResponse:
    try:
        task = request.app.state.repository.resume_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    except InvalidTaskState as exc:
        raise HTTPException(409, "当前任务状态不能继续") from exc
    request.app.state.worker.notify()
    return JSONResponse(task_to_dict(task), status_code=202)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(request: Request, task_id: str) -> Response:
    settings = request.app.state.settings
    try:
        request.app.state.repository.delete_task(
            task_id,
            cleanup=lambda: delete_task_directory(
                settings.data_dir,
                task_id,
            ),
        )
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    except InvalidTaskState as exc:
        raise HTTPException(409, "当前任务状态不能删除") from exc
    except StorageError as exc:
        raise HTTPException(500, str(exc)) from exc
    return Response(status_code=204)


@router.get("/tasks/events")
async def task_events(request: Request) -> StreamingResponse:
    generator = task_event_stream(
        request.app.state.repository,
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
