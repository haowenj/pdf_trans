from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pdf_trans.web.config import WebSettings
from pdf_trans.web.db import make_engine, make_session_factory, run_migrations
from pdf_trans.web.repository import TaskRepository
from pdf_trans.web.routes.files import router as files_router
from pdf_trans.web.routes.logs import router as logs_router
from pdf_trans.web.routes.pages import router as pages_router
from pdf_trans.web.routes.tasks import router as tasks_router
from pdf_trans.web.task_logging import TaskLogHandler
from pdf_trans.web.task_runner import TaskRunner
from pdf_trans.web.worker import TaskWorker


def create_app(
    settings: WebSettings | None = None,
    *,
    repository: TaskRepository | None = None,
    worker: TaskWorker | None = None,
) -> FastAPI:
    settings = settings or WebSettings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if repository is None:
        engine = make_engine(settings.database_url)
        run_migrations(engine)
        repository = TaskRepository(make_session_factory(engine))
    task_log_handler = (
        None
        if worker is not None
        else TaskLogHandler(repository, settings.data_dir)
    )
    if worker is None:
        worker = TaskWorker(
            repository,
            TaskRunner(settings),
            task_log_handler,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository.interrupt_running()
        package_logger = logging.getLogger("pdf_trans")
        if task_log_handler is not None:
            package_logger.addHandler(task_log_handler)
            package_logger.setLevel(logging.INFO)
        worker.start()
        try:
            yield
        finally:
            worker.stop()
            if task_log_handler is not None:
                package_logger.removeHandler(task_log_handler)

    app = FastAPI(title="PDF Trans", lifespan=lifespan)
    app.state.settings = settings
    app.state.repository = repository
    app.state.worker = worker
    app.mount(
        "/static",
        StaticFiles(
            directory=str(Path(__file__).with_name("static")),
            check_dir=False,
        ),
        name="static",
    )
    app.include_router(pages_router)
    app.include_router(files_router)
    app.include_router(tasks_router)
    app.include_router(logs_router)
    return app
