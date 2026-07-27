from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = (
        {"check_same_thread": False}
        if url.get_backend_name() == "sqlite"
        else {}
    )
    return create_engine(database_url, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def run_migrations(engine: Engine) -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).with_name("migrations")),
    )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
