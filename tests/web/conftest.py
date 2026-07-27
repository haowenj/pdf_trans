import pytest
from fastapi.testclient import TestClient

from pdf_trans.web.app import create_app
from pdf_trans.web.config import WebSettings
from pdf_trans.web.db import (
    make_engine,
    make_session_factory,
    run_migrations,
)
from pdf_trans.web.repository import TaskRepository


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def repository(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'web.db'}")
    run_migrations(engine)
    return TaskRepository(make_session_factory(engine))


class FakeWorker:
    def __init__(self):
        self.start_count = 0
        self.stop_count = 0
        self.notify_count = 0

    def start(self):
        self.start_count += 1

    def stop(self):
        self.stop_count += 1

    def notify(self):
        self.notify_count += 1


@pytest.fixture
def web_settings(tmp_path):
    return WebSettings.from_env(environ={}, project_root=tmp_path)


@pytest.fixture
def web_client(repository, web_settings):
    worker = FakeWorker()
    app = create_app(
        web_settings,
        repository=repository,
        worker=worker,
    )
    with TestClient(app) as client:
        yield client
