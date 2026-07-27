import pytest

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
