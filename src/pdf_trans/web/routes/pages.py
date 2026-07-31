from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from pdf_trans.web.time_display import format_beijing_time

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).parents[1] / "templates")
)
templates.env.filters["beijing_time"] = format_beijing_time


@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tasks": request.app.state.repository.list_tasks(),
            "max_upload_mib": request.app.state.settings.max_upload_mib,
        },
    )
