from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from pdf_trans.web.db import (
    make_engine,
    make_session_factory,
    run_migrations,
)
from pdf_trans.web.models import Task, TaskLog


def test_migration_creates_portable_task_tables(tmp_path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'web.db'}")

    run_migrations(engine)

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "alembic_version",
        "tasks",
        "task_logs",
    }
    task_columns = {
        column["name"]: column for column in inspector.get_columns("tasks")
    }
    assert task_columns["id"]["type"].length == 36
    assert task_columns["status"]["type"].length == 16
    assert task_columns["error_message"]["type"].length == 2000
    assert {index["name"] for index in inspector.get_indexes("tasks")} >= {
        "ix_tasks_status",
        "ix_tasks_created_at",
    }


def test_models_round_trip_without_sqlite_specific_values(tmp_path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'web.db'}")
    run_migrations(engine)
    sessions = make_session_factory(engine)

    with sessions.begin() as session:
        session.add(
            Task(
                id="00000000-0000-0000-0000-000000000001",
                original_filename="paper.pdf",
                status="queued",
                source_pdf_path="tasks/id/upload/source.pdf",
            )
        )
        session.add(
            TaskLog(
                task_id="00000000-0000-0000-0000-000000000001",
                level="INFO",
                message="已排队",
            )
        )

    with sessions() as session:
        assert (
            session.get(Task, "00000000-0000-0000-0000-000000000001").status
            == "queued"
        )
        assert session.query(TaskLog).one().message == "已排队"


def test_task_schema_compiles_for_mysql_without_native_enum_or_json() -> None:
    task_ddl = str(
        CreateTable(Task.__table__).compile(dialect=mysql.dialect())
    ).upper()
    log_ddl = str(
        CreateTable(TaskLog.__table__).compile(dialect=mysql.dialect())
    ).upper()

    assert "CREATE TABLE TASKS" in task_ddl
    assert "CREATE TABLE TASK_LOGS" in log_ddl
    assert "ENUM" not in task_ddl
    assert "JSON" not in task_ddl + log_ddl
