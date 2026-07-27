from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from markupsafe import Markup

from pdf_trans.web.markdown import render_safe_markdown
from pdf_trans.web.repository import TaskNotFound, TaskView
from pdf_trans.web.routes.pages import templates
from pdf_trans.web.storage import (
    StorageError,
    resolve_stored_path,
    resolve_task_asset,
)

router = APIRouter()


def _completed_task(request: Request, task_id: str) -> tuple[TaskView, Path]:
    try:
        task = request.app.state.repository.get_task(task_id)
    except TaskNotFound as exc:
        raise HTTPException(404, "任务不存在") from exc
    if task.status != "succeeded" or task.markdown_path is None:
        raise HTTPException(409, "任务尚未生成 Markdown")
    try:
        markdown = resolve_stored_path(
            request.app.state.settings.data_dir,
            task.markdown_path,
        )
    except (StorageError, OSError) as exc:
        raise HTTPException(404, "Markdown 文件不存在") from exc
    return task, markdown


@router.get("/tasks/{task_id}/view")
def reader(request: Request, task_id: str):
    task, markdown = _completed_task(request, task_id)
    try:
        source = markdown.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(404, "Markdown 文件不存在") from exc
    content = render_safe_markdown(
        source,
        asset_base_url=f"/tasks/{task.id}/assets",
    )
    return templates.TemplateResponse(
        request,
        "reader.html",
        {"task": task, "content": Markup(content)},
    )


@router.get("/tasks/{task_id}/markdown")
def markdown_download(request: Request, task_id: str) -> FileResponse:
    task, markdown = _completed_task(request, task_id)
    filename = f"{Path(task.original_filename).stem}-translated.md"
    return FileResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        filename=filename,
    )


@router.get("/tasks/{task_id}/assets/{asset_path:path}")
def task_asset(
    request: Request,
    task_id: str,
    asset_path: str,
) -> FileResponse:
    _, markdown = _completed_task(request, task_id)
    try:
        asset = resolve_task_asset(markdown, asset_path)
    except (StorageError, OSError) as exc:
        raise HTTPException(404, "任务资源不存在") from exc
    return FileResponse(asset)
