from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).parents[1] / "templates")
)


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
