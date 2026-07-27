# PDF Web Task Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-process Web interface that accepts PDF uploads, runs the existing workflow asynchronously in a persisted serial queue, streams task logs, resumes interrupted translations, and safely previews, downloads, or prints the generated Markdown.

**Architecture:** A server-rendered FastAPI application owns a SQLAlchemy repository, a single daemon worker thread, and Jinja2 templates with small vanilla-JavaScript controllers. SQLite is the default persistence layer, Alembic owns schema changes, all generated files remain in per-task directories, and the existing workflow functions remain the only PDF-processing implementation.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Jinja2, SQLAlchemy 2.x, Alembic, markdown-it-py, nh3, local KaTeX 0.18.1 assets, vanilla JavaScript, CSS, pytest.

## Global Constraints

- Work only in `/Users/wenjuhao/code/python/pdf_trans/.worktrees/web-task-dashboard` on branch `experiment/web-task-dashboard`; do not modify `main`.
- Keep `python -m pdf_trans` and all 130 existing tests behaviorally unchanged.
- The Web service runs as one process with one PDF task at a time; do not add Redis, Celery, RQ, Node.js build tooling, task cancellation, deletion, authentication, or server-side PDF generation.
- Keep Web Python dependencies in the `web` optional dependency group.
- Use SQLAlchemy 2.x ORM and Alembic without SQLite-only columns, JSON, native Enum, partial indexes, or raw dialect-specific queries.
- Read the database from `PDF_TRANS_DATABASE_URL`; default to SQLite under `data/web/pdf_trans.db`, while preserving MySQL-compatible schema and transactions.
- Store task paths relative to `PDF_TRANS_WEB_DATA_DIR`; never derive a filesystem path from the uploaded filename.
- Use task states `queued`, `running`, `succeeded`, `failed`, and `interrupted`.
- Default `PDF_TRANS_MAX_UPLOAD_MIB` to `200`, `PDF_TRANS_MINERU_URL` to `http://127.0.0.1:7100`, `PDF_TRANS_WEB_HOST` to `127.0.0.1`, and `PDF_TRANS_WEB_PORT` to `8000`.
- Serve every browser dependency locally. Vendor KaTeX at exactly `0.18.1` with its license and integrity record.
- Enable raw HTML in Markdown only before running the complete HTML through an nh3 allowlist.
- Configure KaTeX with `trust: false`, `throwOnError: false`, bounded `maxSize`, and bounded `maxExpand`.
- Use browser printing through `window.print()`; do not generate a second PDF artifact on the server.
- Follow strict red-green-refactor: no production behavior is added before its focused test has failed for the expected reason.

## File Map

Create these focused modules:

```text
src/pdf_trans/web/
  __init__.py                 Public Web package marker.
  __main__.py                 `python -m pdf_trans.web` launcher.
  app.py                      FastAPI factory and lifespan ownership.
  config.py                   Environment-backed immutable settings.
  db.py                       Engine/session creation and Alembic runner.
  models.py                   Portable SQLAlchemy task/log mappings.
  repository.py               Task state machine and log queries.
  storage.py                  Upload, stored-path, and asset-path safety.
  task_runner.py              Full-run versus translation-resume decision.
  task_logging.py             Single-active-task logging handler.
  worker.py                   Serial daemon worker.
  markdown.py                 Markdown conversion and nh3 sanitization.
  streams.py                  Reusable task/log SSE generators.
  routes/
    __init__.py
    pages.py                  Dashboard and reader HTML pages.
    tasks.py                  Upload, resume, and task-state SSE.
    logs.py                   Log history and log SSE.
    files.py                  Markdown download and safe task assets.
  templates/
    dashboard.html
    reader.html
  static/
    app.css
    dashboard.js
    reader.js
    vendor/katex/
      katex.min.css
      katex.min.js
      auto-render.min.js
      LICENSE.txt
      SOURCE.txt
      fonts/*
  migrations/
    env.py
    script.py.mako
    versions/0001_create_web_tasks.py
```

Create these tests:

```text
tests/web/
  conftest.py
  test_config.py
  test_db.py
  test_repository.py
  test_storage.py
  test_task_runner.py
  test_task_logging.py
  test_worker.py
  test_markdown.py
  test_streams.py
  test_task_routes.py
  test_file_routes.py
  test_pages.py
  test_web_entrypoint.py
```

---

### Task 1: Web Dependencies and Immutable Settings

**Files:**
- Modify: `pyproject.toml`
- Create: `src/pdf_trans/web/__init__.py`
- Create: `src/pdf_trans/web/config.py`
- Create: `tests/web/test_config.py`

**Interfaces:**
- Consumes: existing project root convention from `src/pdf_trans/workflow.py`.
- Produces: `WebSettings.from_env(environ=None, project_root=None) -> WebSettings` and `WebSettings.max_upload_bytes -> int`.

- [ ] **Step 1: Write failing settings tests**

```python
# tests/web/test_config.py
from pathlib import Path

import pytest

from pdf_trans.web.config import WebSettings


def test_settings_use_portable_defaults(tmp_path):
    settings = WebSettings.from_env(environ={}, project_root=tmp_path)

    assert settings.data_dir == (tmp_path / "data/web").resolve()
    assert settings.database_url == (
        f"sqlite:///{(tmp_path / 'data/web/pdf_trans.db').resolve()}"
    )
    assert settings.max_upload_mib == 200
    assert settings.max_upload_bytes == 200 * 1024 * 1024
    assert settings.mineru_url == "http://127.0.0.1:7100"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_settings_read_web_environment(tmp_path):
    settings = WebSettings.from_env(
        environ={
            "PDF_TRANS_WEB_DATA_DIR": str(tmp_path / "runtime"),
            "PDF_TRANS_DATABASE_URL": "mysql+pymysql://u:p@db/pdf_trans",
            "PDF_TRANS_MAX_UPLOAD_MIB": "12",
            "PDF_TRANS_MINERU_URL": "http://mineru:7200/",
            "PDF_TRANS_WEB_HOST": "0.0.0.0",
            "PDF_TRANS_WEB_PORT": "9000",
        },
        project_root=tmp_path,
    )

    assert settings.data_dir == (tmp_path / "runtime").resolve()
    assert settings.database_url == "mysql+pymysql://u:p@db/pdf_trans"
    assert settings.max_upload_bytes == 12 * 1024 * 1024
    assert settings.mineru_url == "http://mineru:7200"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PDF_TRANS_MAX_UPLOAD_MIB", "0"),
        ("PDF_TRANS_MAX_UPLOAD_MIB", "1.5"),
        ("PDF_TRANS_WEB_PORT", "0"),
        ("PDF_TRANS_WEB_PORT", "65536"),
    ],
)
def test_settings_reject_invalid_positive_integers(tmp_path, name, value):
    with pytest.raises(ValueError, match=name):
        WebSettings.from_env(
            environ={name: value},
            project_root=tmp_path,
        )
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest tests/web/test_config.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'pdf_trans.web'`.

- [ ] **Step 3: Add the Web dependency group and settings implementation**

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
test = [
    "build>=1.2,<2",
    "pytest>=8,<9",
]
web = [
    "alembic>=1.16,<2",
    "fastapi>=0.116,<1",
    "jinja2>=3.1,<4",
    "markdown-it-py>=4,<5",
    "nh3>=0.3,<1",
    "python-multipart>=0.0.20,<1",
    "sqlalchemy>=2.0,<3",
    "uvicorn>=0.35,<1",
]

[tool.setuptools.package-data]
"pdf_trans.web" = [
    "templates/*.html",
    "static/*.css",
    "static/*.js",
    "static/vendor/katex/*.css",
    "static/vendor/katex/*.js",
    "static/vendor/katex/*.txt",
    "static/vendor/katex/fonts/*",
    "migrations/*.py",
    "migrations/*.mako",
    "migrations/versions/*.py",
]
```

Create `src/pdf_trans/web/__init__.py`:

```python
"""Single-process Web interface for PDF Trans."""
```

Create `src/pdf_trans/web/config.py`:

```python
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MINERU_URL = "http://127.0.0.1:7100"


def _positive_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    maximum: int | None = None,
) -> int:
    raw = environ.get(name, str(default))
    if not raw.isdigit():
        raise ValueError(f"{name} 必须是正整数")
    value = int(raw)
    if value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} 必须是正整数")
    return value


@dataclass(frozen=True)
class WebSettings:
    data_dir: Path
    database_url: str
    max_upload_mib: int
    mineru_url: str
    host: str
    port: int

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mib * 1024 * 1024

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> "WebSettings":
        values = os.environ if environ is None else environ
        root = (
            Path(__file__).resolve().parents[3]
            if project_root is None
            else project_root.resolve()
        )
        data_dir = Path(
            values.get("PDF_TRANS_WEB_DATA_DIR", str(root / "data/web"))
        ).expanduser().resolve()
        database_url = values.get(
            "PDF_TRANS_DATABASE_URL",
            f"sqlite:///{(data_dir / 'pdf_trans.db').resolve()}",
        )
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            max_upload_mib=_positive_int(
                values, "PDF_TRANS_MAX_UPLOAD_MIB", 200
            ),
            mineru_url=values.get(
                "PDF_TRANS_MINERU_URL", DEFAULT_MINERU_URL
            ).rstrip("/"),
            host=values.get("PDF_TRANS_WEB_HOST", "127.0.0.1"),
            port=_positive_int(
                values, "PDF_TRANS_WEB_PORT", 8000, maximum=65535
            ),
        )
```

- [ ] **Step 4: Install Web dependencies and verify GREEN**

Run: `python3 -m pip install -e '.[test,web]'`

Expected: editable install completes without dependency conflicts.

Run: `python3 -m pytest tests/web/test_config.py -v`

Expected: all settings tests pass.

- [ ] **Step 5: Commit the settings foundation**

```bash
git add pyproject.toml src/pdf_trans/web tests/web/test_config.py
git commit -m "feat: add web settings and dependencies"
```

---

### Task 2: Portable Models and Alembic Migration

**Files:**
- Create: `src/pdf_trans/web/models.py`
- Create: `src/pdf_trans/web/db.py`
- Create: `src/pdf_trans/web/migrations/env.py`
- Create: `src/pdf_trans/web/migrations/script.py.mako`
- Create: `src/pdf_trans/web/migrations/versions/0001_create_web_tasks.py`
- Create: `tests/web/test_db.py`

**Interfaces:**
- Consumes: `WebSettings.database_url`.
- Produces: `Base`, `Task`, `TaskLog`, `make_engine(url)`, `make_session_factory(engine)`, and `run_migrations(engine)`.

- [ ] **Step 1: Write failing migration and schema tests**

```python
# tests/web/test_db.py
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from pdf_trans.web.db import (
    make_engine,
    make_session_factory,
    run_migrations,
)
from pdf_trans.web.models import Task, TaskLog


def test_migration_creates_portable_task_tables(tmp_path):
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


def test_models_round_trip_without_sqlite_specific_values(tmp_path):
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
        assert session.get(
            Task, "00000000-0000-0000-0000-000000000001"
        ).status == "queued"
        assert session.query(TaskLog).one().message == "已排队"


def test_task_schema_compiles_for_mysql_without_native_enum_or_json():
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
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m pytest tests/web/test_db.py -v`

Expected: import fails because `pdf_trans.web.db` and `models` do not exist.

- [ ] **Step 3: Implement the portable mappings**

Create `src/pdf_trans/web/models.py` with:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    source_pdf_path: Mapped[str] = mapped_column(String(1024))
    normalized_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    markdown_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskLog(Base):
    __tablename__ = "task_logs"
    __table_args__ = (Index("ix_task_logs_task_id_id", "task_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE")
    )
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
```

- [ ] **Step 4: Implement engine/session creation and packaged Alembic execution**

Create `src/pdf_trans/web/db.py`:

```python
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
```

Create `src/pdf_trans/web/migrations/env.py`:

```python
from alembic import context

from pdf_trans.web.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = context.config.attributes["connection"]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
```

Create `src/pdf_trans/web/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `src/pdf_trans/web/migrations/versions/0001_create_web_tasks.py`:

```python
from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_web_tasks"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("source_pdf_path", sa.String(length=1024), nullable=False),
        sa.Column("normalized_path", sa.String(length=1024), nullable=True),
        sa.Column("markdown_path", sa.String(length=1024), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_table(
        "task_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_logs_task_id_id", "task_logs", ["task_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_logs_task_id_id", table_name="task_logs")
    op.drop_table("task_logs")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
```

- [ ] **Step 5: Run migration tests and verify GREEN**

Run: `python3 -m pytest tests/web/test_db.py -v`

Expected: both schema tests pass on a temporary SQLite file.

Run: `python3 -m pytest tests/test_package_name.py -v`

Expected: the existing package-name constraint still passes.

- [ ] **Step 6: Commit persistence schema**

```bash
git add src/pdf_trans/web tests/web/test_db.py
git commit -m "feat: add web task database schema"
```

---

### Task 3: Repository State Machine and Persisted Logs

**Files:**
- Create: `src/pdf_trans/web/repository.py`
- Create: `tests/web/conftest.py`
- Create: `tests/web/test_repository.py`

**Interfaces:**
- Consumes: `sessionmaker[Session]`, `Task`, and `TaskLog`.
- Produces: immutable `TaskView`, `LogView`, and `TaskRepository` methods used by every later task.

- [ ] **Step 1: Add a migrated repository fixture and failing lifecycle tests**

```python
# tests/web/conftest.py
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
```

```python
# tests/web/test_repository.py
import pytest

from pdf_trans.web.repository import InvalidTaskState, TaskNotFound


def test_repository_claims_oldest_task_and_increments_attempt(repository):
    second = repository.create_task(
        "b", "b.pdf", "tasks/b/upload/source.pdf"
    )
    first = repository.create_task(
        "a", "a.pdf", "tasks/a/upload/source.pdf"
    )

    claimed = repository.claim_next_task()

    assert claimed.id == second.id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert repository.get_task(first.id).status == "queued"


def test_repository_interrupts_and_resumes_only_recoverable_states(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    repository.claim_next_task()

    assert repository.interrupt_running() == 1
    assert repository.get_task("a").status == "interrupted"
    resumed = repository.resume_task("a")
    assert resumed.status == "queued"
    assert resumed.error_message is None

    with pytest.raises(InvalidTaskState):
        repository.resume_task("a")
    with pytest.raises(TaskNotFound):
        repository.resume_task("missing")


def test_repository_persists_ordered_logs_and_success_artifacts(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    repository.claim_next_task()
    first = repository.append_log("a", "INFO", "开始")
    second = repository.append_log("a", "WARN", "重试")
    repository.mark_succeeded(
        "a",
        normalized_path="tasks/a/attempts/1/paper/normalized_content_list.json",
        markdown_path="tasks/a/attempts/1/paper/rendered.md",
    )

    assert [log.id for log in repository.list_logs("a")] == [
        first.id,
        second.id,
    ]
    assert [log.message for log in repository.list_logs("a", after_id=first.id)] == [
        "重试"
    ]
    task = repository.get_task("a")
    assert task.status == "succeeded"
    assert task.finished_at is not None
```

- [ ] **Step 2: Run repository tests and verify RED**

Run: `python3 -m pytest tests/web/test_repository.py -v`

Expected: import fails because `pdf_trans.web.repository` does not exist.

- [ ] **Step 3: Implement immutable views and exact repository operations**

Create `src/pdf_trans/web/repository.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session, sessionmaker

from pdf_trans.web.models import Task, TaskLog, utc_now

RECOVERABLE_STATES = {"failed", "interrupted"}


class TaskNotFound(LookupError):
    pass


class InvalidTaskState(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskView:
    id: str
    original_filename: str
    status: str
    attempt_count: int
    source_pdf_path: str
    normalized_path: str | None
    markdown_path: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class LogView:
    id: int
    task_id: str
    level: str
    message: str
    created_at: datetime


def _task_view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        original_filename=task.original_filename,
        status=task.status,
        attempt_count=task.attempt_count,
        source_pdf_path=task.source_pdf_path,
        normalized_path=task.normalized_path,
        markdown_path=task.markdown_path,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def _log_view(log: TaskLog) -> LogView:
    return LogView(
        id=log.id,
        task_id=log.task_id,
        level=log.level,
        message=log.message,
        created_at=log.created_at,
    )


class TaskRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_task(
        self,
        task_id: str,
        original_filename: str,
        source_pdf_path: str,
    ) -> TaskView:
        with self._sessions.begin() as session:
            task = Task(
                id=task_id,
                original_filename=original_filename,
                status="queued",
                source_pdf_path=source_pdf_path,
            )
            session.add(task)
            session.flush()
            return _task_view(task)

    def get_task(self, task_id: str) -> TaskView:
        with self._sessions() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            return _task_view(task)

    def list_tasks(self, limit: int = 100) -> list[TaskView]:
        statement: Select[tuple[Task]] = (
            select(Task).order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)
        )
        with self._sessions() as session:
            return [_task_view(task) for task in session.scalars(statement)]

    def claim_next_task(self) -> TaskView | None:
        with self._sessions.begin() as session:
            task_id = session.scalar(
                select(Task.id)
                .where(Task.status == "queued")
                .order_by(Task.created_at, Task.id)
                .limit(1)
            )
            if task_id is None:
                return None
            now = utc_now()
            result = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == "queued")
                .values(
                    status="running",
                    attempt_count=Task.attempt_count + 1,
                    started_at=now,
                    finished_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None
            task = session.get(Task, task_id)
            return _task_view(task)

    def interrupt_running(self) -> int:
        now = utc_now()
        with self._sessions.begin() as session:
            result = session.execute(
                update(Task)
                .where(Task.status == "running")
                .values(
                    status="interrupted",
                    error_message="服务在任务完成前停止，可继续执行",
                    finished_at=now,
                    updated_at=now,
                )
            )
            return int(result.rowcount)

    def resume_task(self, task_id: str) -> TaskView:
        now = utc_now()
        with self._sessions.begin() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            if task.status not in RECOVERABLE_STATES:
                raise InvalidTaskState(task.status)
            task.status = "queued"
            task.error_message = None
            task.finished_at = None
            task.updated_at = now
            session.flush()
            return _task_view(task)

    def mark_succeeded(
        self,
        task_id: str,
        *,
        normalized_path: str,
        markdown_path: str,
    ) -> TaskView:
        return self._finish(
            task_id,
            status="succeeded",
            normalized_path=normalized_path,
            markdown_path=markdown_path,
            error_message=None,
        )

    def mark_failed(self, task_id: str, error_message: str) -> TaskView:
        return self._finish(
            task_id,
            status="failed",
            error_message=error_message[:2000],
        )

    def _finish(self, task_id: str, *, status: str, **values) -> TaskView:
        now = utc_now()
        with self._sessions.begin() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            for name, value in values.items():
                setattr(task, name, value)
            task.status = status
            task.finished_at = now
            task.updated_at = now
            session.flush()
            return _task_view(task)

    def append_log(
        self, task_id: str, level: str, message: str
    ) -> LogView:
        with self._sessions.begin() as session:
            if session.get(Task, task_id) is None:
                raise TaskNotFound(task_id)
            log = TaskLog(
                task_id=task_id,
                level=level[:16],
                message=message,
            )
            session.add(log)
            session.flush()
            return _log_view(log)

    def list_logs(
        self,
        task_id: str,
        *,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[LogView]:
        self.get_task(task_id)
        statement = (
            select(TaskLog)
            .where(TaskLog.task_id == task_id, TaskLog.id > after_id)
            .order_by(TaskLog.id)
            .limit(limit)
        )
        with self._sessions() as session:
            return [_log_view(log) for log in session.scalars(statement)]
```

- [ ] **Step 4: Verify repository behavior**

Run: `python3 -m pytest tests/web/test_repository.py -v`

Expected: lifecycle, recovery, log ordering, and artifact tests pass.

- [ ] **Step 5: Commit repository state machine**

```bash
git add src/pdf_trans/web/repository.py tests/web
git commit -m "feat: persist web task lifecycle and logs"
```

---

### Task 4: Safe PDF Upload and Task File Resolution

**Files:**
- Create: `src/pdf_trans/web/storage.py`
- Create: `tests/web/test_storage.py`

**Interfaces:**
- Consumes: `WebSettings.data_dir` and FastAPI `UploadFile`.
- Produces: `save_pdf_upload(...) -> StoredUpload`, `resolve_stored_path(...) -> Path`, and `resolve_task_asset(...) -> Path`.

- [ ] **Step 1: Write failing upload and traversal tests**

```python
# tests/web/test_storage.py
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from pdf_trans.web.storage import (
    StorageError,
    UploadValidationError,
    resolve_task_asset,
    save_pdf_upload,
)


@pytest.mark.anyio
async def test_save_pdf_uses_uuid_path_and_returns_relative_storage(tmp_path):
    upload = UploadFile(
        filename="../../paper.pdf",
        file=BytesIO(b"%PDF-1.7\nbody"),
        headers={"content-type": "application/pdf"},
    )

    stored = await save_pdf_upload(
        upload,
        task_id="task-id",
        data_dir=tmp_path,
        max_bytes=100,
    )

    assert stored.original_filename == "paper.pdf"
    assert stored.relative_path == "tasks/task-id/upload/source.pdf"
    assert (tmp_path / stored.relative_path).read_bytes() == b"%PDF-1.7\nbody"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content_type", "body", "max_bytes"),
    [
        ("paper.txt", "application/pdf", b"%PDF-x", 100),
        ("paper.pdf", "text/plain", b"%PDF-x", 100),
        ("paper.pdf", "application/pdf", b"not-pdf", 100),
        ("paper.pdf", "application/pdf", b"%PDF-too-large", 5),
    ],
)
async def test_save_pdf_rejects_invalid_uploads(
    tmp_path, filename, content_type, body, max_bytes
):
    upload = UploadFile(
        filename=filename,
        file=BytesIO(body),
        headers={"content-type": content_type},
    )

    with pytest.raises(UploadValidationError):
        await save_pdf_upload(
            upload,
            task_id="task-id",
            data_dir=tmp_path,
            max_bytes=max_bytes,
        )

    assert not (tmp_path / "tasks/task-id/upload/source.pdf").exists()


def test_asset_resolution_stays_below_markdown_directory(tmp_path):
    paper = tmp_path / "tasks/a/attempts/1/paper"
    image = paper / "images/a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    markdown = paper / "rendered.md"
    markdown.write_text("x", encoding="utf-8")

    assert resolve_task_asset(markdown, "images/a.png") == image.resolve()
    with pytest.raises(StorageError):
        resolve_task_asset(markdown, "../../upload/source.pdf")
```

- [ ] **Step 2: Run storage tests and verify RED**

Run: `python3 -m pytest tests/web/test_storage.py -v`

Expected: import fails because `pdf_trans.web.storage` does not exist.

- [ ] **Step 3: Implement chunked validation and safe resolution**

Create `src/pdf_trans/web/storage.py`:

```python
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath

from starlette.datastructures import UploadFile

CHUNK_SIZE = 1024 * 1024
PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


class StorageError(RuntimeError):
    pass


class UploadValidationError(StorageError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    original_filename: str
    relative_path: str


async def save_pdf_upload(
    upload: UploadFile,
    *,
    task_id: str,
    data_dir: Path,
    max_bytes: int,
) -> StoredUpload:
    original = PurePath(upload.filename or "").name[:255]
    if not original or Path(original).suffix.lower() != ".pdf":
        raise UploadValidationError("只能上传扩展名为 .pdf 的文件")
    if upload.content_type not in PDF_CONTENT_TYPES:
        raise UploadValidationError("上传文件的 Content-Type 必须是 PDF")

    relative = Path("tasks") / task_id / "upload/source.pdf"
    target = data_dir / relative
    temporary = target.with_suffix(".pdf.part")
    target.parent.mkdir(parents=True, exist_ok=False)
    size = 0
    prefix = b""
    try:
        with temporary.open("xb") as handle:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError("PDF 超过上传大小限制")
                if len(prefix) < 5:
                    prefix = (prefix + chunk)[:5]
                handle.write(chunk)
        if prefix != b"%PDF-":
            raise UploadValidationError("文件头不是有效 PDF")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(target.parents[1], ignore_errors=True)
        raise
    finally:
        await upload.close()
    return StoredUpload(original, relative.as_posix())


def resolve_stored_path(data_dir: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StorageError("任务文件路径越界")
    root = data_dir.resolve()
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise StorageError("任务文件路径越界")
    return candidate


def resolve_task_asset(markdown_path: Path, asset_path: str) -> Path:
    relative = Path(asset_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise StorageError("任务资源路径越界")
    root = markdown_path.resolve(strict=True).parent
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise StorageError("任务资源路径越界")
    return candidate
```

- [ ] **Step 4: Verify storage GREEN**

Run: `python3 -m pytest tests/web/test_storage.py -v`

Expected: all upload validation and traversal tests pass.

- [ ] **Step 5: Commit safe task storage**

```bash
git add src/pdf_trans/web/storage.py tests/web/test_storage.py
git commit -m "feat: validate and isolate PDF uploads"
```

---

### Task 5: Resume-Aware Task Runner

**Files:**
- Create: `src/pdf_trans/web/task_runner.py`
- Create: `tests/web/test_task_runner.py`

**Interfaces:**
- Consumes: `WebSettings`, `TaskView`, existing `process_pdf`, `process_translation_file`, and `render_content_list_file`.
- Produces: `TaskRunner.run(task) -> TaskArtifacts` without changing database state.

- [ ] **Step 1: Write failing full-run, resume, and ambiguous-checkpoint tests**

Use small fake result objects and injected callables:

```python
# tests/web/test_task_runner.py
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf_trans.web.config import WebSettings
from pdf_trans.web.repository import TaskView
from pdf_trans.web.task_runner import (
    AmbiguousCheckpoint,
    TaskRunner,
    WorkflowServices,
)


def task_view(tmp_path, *, attempt_count=1):
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    return TaskView(
        id="a",
        original_filename="paper.pdf",
        status="running",
        attempt_count=attempt_count,
        source_pdf_path="tasks/a/upload/source.pdf",
        normalized_path=None,
        markdown_path=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def settings(tmp_path):
    return WebSettings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'db'}",
        max_upload_mib=200,
        mineru_url="http://mineru:7100",
        host="127.0.0.1",
        port=8000,
    )


def test_runner_starts_full_workflow_when_no_checkpoint_exists(tmp_path):
    source = tmp_path / "tasks/a/upload/source.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-")
    received = {}

    def full(path, *, svr_url, data_dir):
        received.update(path=path, svr_url=svr_url, data_dir=data_dir)
        normalized = data_dir / "paper/normalized_content_list.json"
        markdown = data_dir / "paper/rendered.md"
        normalized.parent.mkdir(parents=True)
        normalized.write_text("[]", encoding="utf-8")
        markdown.write_text("# 译文", encoding="utf-8")
        return SimpleNamespace(
            normalized_path=normalized,
            markdown_path=markdown,
            before_count=1,
            filtered_count=0,
            candidate_count=0,
            translation_stats=SimpleNamespace(
                success_count=1, failed_count=0
            ),
        )

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(full, lambda path: None, lambda source, output: None),
    )
    result = runner.run(task_view(tmp_path))

    assert received["data_dir"] == tmp_path / "tasks/a/attempts/1"
    assert result.normalized_path.endswith("normalized_content_list.json")
    assert result.markdown_path.endswith("rendered.md")


def test_runner_resumes_translation_and_renders_markdown(tmp_path):
    normalized = (
        tmp_path
        / "tasks/a/attempts/1/paper/normalized_content_list.json"
    )
    normalized.parent.mkdir(parents=True)
    normalized.write_text("[]", encoding="utf-8")
    translated = normalized.with_name("translated_content_list.json")
    translated.write_text("[]", encoding="utf-8")
    calls = []

    def translate(path):
        calls.append(("translate", path))
        return SimpleNamespace(
            normalized_path=path,
            translated_path=translated,
            stats=SimpleNamespace(success_count=3, failed_count=0),
        )

    def render(source, output):
        calls.append(("render", source, output))
        output.write_text("# resumed", encoding="utf-8")

    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(lambda *args, **kwargs: None, translate, render),
    )
    result = runner.run(task_view(tmp_path, attempt_count=2))

    assert calls[0] == ("translate", normalized)
    assert calls[1][2] == normalized.with_name("rendered.md")
    assert result.resumed is True


def test_runner_rejects_multiple_normalized_checkpoints(tmp_path):
    for attempt in ("1", "2"):
        path = (
            tmp_path
            / f"tasks/a/attempts/{attempt}/paper/normalized_content_list.json"
        )
        path.parent.mkdir(parents=True)
        path.write_text("[]", encoding="utf-8")
    runner = TaskRunner(
        settings(tmp_path),
        WorkflowServices(None, None, None),
    )

    with pytest.raises(AmbiguousCheckpoint):
        runner.run(task_view(tmp_path, attempt_count=3))
```

- [ ] **Step 2: Run task-runner tests and verify RED**

Run: `python3 -m pytest tests/web/test_task_runner.py -v`

Expected: import fails because `pdf_trans.web.task_runner` does not exist.

- [ ] **Step 3: Implement injected workflow selection**

Create `src/pdf_trans/web/task_runner.py` with these exact public types:

```python
@dataclass(frozen=True)
class WorkflowServices:
    process_pdf: Callable[..., WorkflowResult]
    process_translation_file: Callable[..., TranslationFileResult]
    render_content_list_file: Callable[[Path, Path], None]


@dataclass(frozen=True)
class TaskArtifacts:
    normalized_path: str
    markdown_path: str
    resumed: bool
    summary: str


class AmbiguousCheckpoint(RuntimeError):
    pass


class TaskRunner:
    def __init__(
        self,
        settings: WebSettings,
        services: WorkflowServices | None = None,
    ) -> None:
        self.settings = settings
        self.services = services or WorkflowServices(
            process_pdf,
            process_translation_file,
            render_content_list_file,
        )

    def run(self, task: TaskView) -> TaskArtifacts:
        source = resolve_stored_path(
            self.settings.data_dir, task.source_pdf_path
        )
        task_root = self.settings.data_dir / "tasks" / task.id
        checkpoints = sorted(
            task_root.glob(
                "attempts/*/**/normalized_content_list.json"
            )
        )
        if len(checkpoints) > 1:
            raise AmbiguousCheckpoint(
                "发现多个 normalized_content_list.json，无法选择断点"
            )
        if checkpoints:
            normalized = checkpoints[0]
            result = self.services.process_translation_file(normalized)
            markdown = normalized.with_name("rendered.md")
            self.services.render_content_list_file(
                result.translated_path, markdown
            )
            summary = (
                f"断点续传完成：成功 {result.stats.success_count} 段，"
                f"失败 {result.stats.failed_count} 段"
            )
            return self._artifacts(normalized, markdown, True, summary)

        attempt_dir = (
            task_root / "attempts" / str(task.attempt_count)
        )
        result = self.services.process_pdf(
            source,
            svr_url=self.settings.mineru_url,
            data_dir=attempt_dir,
        )
        summary = (
            f"完整工作流完成：输入 {result.before_count} 项，"
            f"过滤 {result.filtered_count} 项，"
            f"跨页候选 {result.candidate_count} 个，"
            f"翻译成功 {result.translation_stats.success_count} 段，"
            f"失败 {result.translation_stats.failed_count} 段"
        )
        return self._artifacts(
            result.normalized_path,
            result.markdown_path,
            False,
            summary,
        )

    def _artifacts(
        self,
        normalized: Path,
        markdown: Path,
        resumed: bool,
        summary: str,
    ) -> TaskArtifacts:
        root = self.settings.data_dir.resolve()
        return TaskArtifacts(
            normalized.resolve().relative_to(root).as_posix(),
            markdown.resolve().relative_to(root).as_posix(),
            resumed,
            summary,
        )
```

Import `WorkflowResult` and `TranslationFileResult` from
`pdf_trans.workflow`, the three existing workflow functions from their current
modules, and `resolve_stored_path` from `pdf_trans.web.storage`. In the test
helper, construct timestamps with one fixed timezone-aware `datetime` value so
the arguments match the production `TaskView` type.

- [ ] **Step 4: Verify runner GREEN**

Run: `python3 -m pytest tests/web/test_task_runner.py -v`

Expected: full workflow uses `attempts/1`, resume skips MinerU, and ambiguous checkpoints fail.

- [ ] **Step 5: Commit resume-aware execution**

```bash
git add src/pdf_trans/web/task_runner.py tests/web/test_task_runner.py
git commit -m "feat: resume interrupted web translation tasks"
```

---

### Task 6: Task-Scoped Logging and Serial Worker

**Files:**
- Create: `src/pdf_trans/web/task_logging.py`
- Create: `src/pdf_trans/web/worker.py`
- Create: `tests/web/test_task_logging.py`
- Create: `tests/web/test_worker.py`

**Interfaces:**
- Consumes: `TaskRepository`, `TaskRunner`, and the package logger.
- Produces: `TaskLogHandler.activate/deactivate`, `TaskWorker.start/stop/notify`, and one-at-a-time execution.

- [ ] **Step 1: Write failing cross-thread logging and serial worker tests**

```python
# tests/web/test_task_logging.py
import logging
from concurrent.futures import ThreadPoolExecutor

from pdf_trans.web.task_logging import TaskLogHandler


def test_handler_captures_child_thread_logs_for_active_task(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    handler = TaskLogHandler(repository)
    package_logger = logging.getLogger("pdf_trans")
    logger = logging.getLogger("pdf_trans.translation")
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    try:
        handler.activate("a")
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(logger.info, "子线程完成").result()
        logging.getLogger("pdf_trans.web.routes").info("请求日志")
        handler.deactivate()
        logger.info("任务外日志")
    finally:
        package_logger.removeHandler(handler)

    assert [log.message for log in repository.list_logs("a")] == [
        "子线程完成"
    ]
```

```python
# tests/web/test_worker.py
import logging
import threading
import time
from types import SimpleNamespace

from pdf_trans.web.task_logging import TaskLogHandler
from pdf_trans.web.worker import TaskWorker


def test_worker_executes_queued_tasks_serially(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    repository.create_task("b", "b.pdf", "tasks/b/upload/source.pdf")
    active = 0
    maximum = 0
    completed = threading.Event()

    class Runner:
        def run(self, task):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            time.sleep(0.01)
            active -= 1
            if task.id == "b":
                completed.set()
            return SimpleNamespace(
                normalized_path=f"tasks/{task.id}/normalized.json",
                markdown_path=f"tasks/{task.id}/rendered.md",
                summary="完成",
            )

    worker = TaskWorker(
        repository, Runner(), TaskLogHandler(repository), wait_seconds=0.01
    )
    worker.start()
    try:
        worker.notify()
        assert completed.wait(1)
    finally:
        worker.stop()

    assert maximum == 1
    assert repository.get_task("a").status == "succeeded"
    assert repository.get_task("b").status == "succeeded"
```

Add this failure test to `tests/web/test_worker.py`:

```python
def test_worker_persists_failure_and_error_log(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")

    class FailingRunner:
        def run(self, task):
            raise RuntimeError("boom")

    worker = TaskWorker(
        repository,
        FailingRunner(),
        TaskLogHandler(repository),
        wait_seconds=0.01,
    )
    package_logger = logging.getLogger("pdf_trans")
    package_logger.addHandler(worker.log_handler)
    package_logger.setLevel(logging.INFO)
    worker.start()
    try:
        worker.notify()
        deadline = time.monotonic() + 1
        while (
            repository.get_task("a").status != "failed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
    finally:
        worker.stop()
        package_logger.removeHandler(worker.log_handler)

    task = repository.get_task("a")
    assert task.status == "failed"
    assert task.error_message == "boom"
    assert any(
        log.level == "ERROR" and "boom" in log.message
        for log in repository.list_logs("a")
    )
```

- [ ] **Step 2: Run worker tests and verify RED**

Run: `python3 -m pytest tests/web/test_task_logging.py tests/web/test_worker.py -v`

Expected: imports fail for the two missing modules.

- [ ] **Step 3: Implement the single-active-task handler**

Create `src/pdf_trans/web/task_logging.py`:

```python
from __future__ import annotations

import logging
import sys

from pdf_trans.web.repository import TaskRepository


class TaskLogHandler(logging.Handler):
    def __init__(self, repository: TaskRepository) -> None:
        super().__init__(logging.INFO)
        self.repository = repository
        self._active_task_id: str | None = None

    def activate(self, task_id: str) -> None:
        with self.lock:
            if self._active_task_id is not None:
                raise RuntimeError("已有活动日志任务")
            self._active_task_id = task_id

    def deactivate(self) -> None:
        with self.lock:
            self._active_task_id = None

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("pdf_trans.web"):
            return
        with self.lock:
            task_id = self._active_task_id
            if task_id is None:
                return
            try:
                self.repository.append_log(
                    task_id, record.levelname, record.getMessage()
                )
            except Exception as exc:
                print(
                    f"无法持久化任务日志：{exc}",
                    file=sys.stderr,
                )
```

Do not apply the colored CLI formatter to stored logs.

- [ ] **Step 4: Implement the daemon serial worker**

Create `src/pdf_trans/web/worker.py`:

```python
from __future__ import annotations

import logging
import threading

from pdf_trans.web.repository import TaskRepository
from pdf_trans.web.task_logging import TaskLogHandler
from pdf_trans.web.task_runner import TaskRunner

LOGGER = logging.getLogger("pdf_trans.task_worker")


class TaskWorker:
    def __init__(
        self,
        repository: TaskRepository,
        runner: TaskRunner,
        log_handler: TaskLogHandler,
        *,
        wait_seconds: float = 1.0,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.log_handler = log_handler
        self.wait_seconds = wait_seconds
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="pdf-trans-web-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.notify()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def notify(self) -> None:
        with self._condition:
            self._condition.notify()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            task = self.repository.claim_next_task()
            if task is None:
                with self._condition:
                    self._condition.wait(timeout=self.wait_seconds)
                continue
            self.log_handler.activate(task.id)
            LOGGER.info(
                "开始第 %d 次执行：%s",
                task.attempt_count,
                task.original_filename,
            )
            try:
                artifacts = self.runner.run(task)
                LOGGER.info("%s", artifacts.summary)
                self.repository.mark_succeeded(
                    task.id,
                    normalized_path=artifacts.normalized_path,
                    markdown_path=artifacts.markdown_path,
                )
            except Exception as exc:
                LOGGER.exception("任务执行失败：%s", exc)
                self.repository.mark_failed(task.id, str(exc))
            finally:
                self.log_handler.deactivate()
```

The daemon thread does not cancel an active external HTTP request. `stop()`
only requests exit, wakes an idle worker, and waits at most one second.

- [ ] **Step 5: Verify worker GREEN**

Run: `python3 -m pytest tests/web/test_task_logging.py tests/web/test_worker.py -v`

Expected: child-thread logs are captured, Web logs are excluded, tasks never overlap, and failures persist.

- [ ] **Step 6: Commit worker and task logs**

```bash
git add src/pdf_trans/web/task_logging.py src/pdf_trans/web/worker.py tests/web
git commit -m "feat: run persisted web tasks serially"
```

---

### Task 7: Safe Markdown Rendering and Asset URL Rewriting

**Files:**
- Create: `src/pdf_trans/web/markdown.py`
- Create: `tests/web/test_markdown.py`

**Interfaces:**
- Consumes: `rendered.md` text and a task-specific asset base URL.
- Produces: `render_safe_markdown(source, asset_base_url) -> str`.

- [ ] **Step 1: Write failing rendering/security tests**

```python
# tests/web/test_markdown.py
from pdf_trans.web.markdown import render_safe_markdown


def test_markdown_keeps_tables_spans_and_rewrites_relative_images():
    source = """
# 标题

<table><tr><td rowspan="2" colspan="3">A $x$</td></tr></table>

![](images/a.png)
"""
    html = render_safe_markdown(
        source, asset_base_url="/tasks/a/assets"
    )

    assert "<h1>标题</h1>" in html
    assert 'rowspan="2"' in html
    assert 'colspan="3"' in html
    assert "A $x$" in html
    assert 'src="/tasks/a/assets/images/a.png"' in html


def test_markdown_removes_dangerous_html_attributes_and_urls():
    html = render_safe_markdown(
        """
<script>alert(1)</script>
<img src="javascript:alert(1)" onerror="alert(2)" style="position:fixed">
<a href="javascript:alert(3)">bad</a>
<iframe src="https://evil.example"></iframe>
""",
        asset_base_url="/tasks/a/assets",
    )

    assert "script" not in html
    assert "alert" not in html
    assert "onerror" not in html
    assert "style=" not in html
    assert "iframe" not in html


def test_markdown_rejects_parent_relative_asset_urls():
    html = render_safe_markdown(
        "![](../../source.pdf)",
        asset_base_url="/tasks/a/assets",
    )
    assert "source.pdf" not in html
```

- [ ] **Step 2: Run renderer tests and verify RED**

Run: `python3 -m pytest tests/web/test_markdown.py -v`

Expected: import fails because `pdf_trans.web.markdown` does not exist.

- [ ] **Step 3: Implement MarkdownIt plus nh3 allowlist**

Create `src/pdf_trans/web/markdown.py`:

```python
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import nh3
from markdown_it import MarkdownIt

PARSER = MarkdownIt("commonmark", {"html": True})
ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "img", "li", "ol", "p", "pre", "strong",
    "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
    "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"rowspan", "colspan"},
    "th": {"rowspan", "colspan"},
}


def render_safe_markdown(source: str, *, asset_base_url: str) -> str:
    rendered = PARSER.render(source)

    def rewrite_relative(url: str) -> str | None:
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return url
        decoded = unquote(parsed.path)
        path = PurePosixPath(decoded)
        if path.is_absolute() or ".." in path.parts:
            return None
        safe_path = "/".join(quote(part, safe="") for part in path.parts)
        if not safe_path:
            return None
        return f"{asset_base_url.rstrip('/')}/{safe_path}"

    return nh3.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        clean_content_tags={
            "script", "style", "iframe", "form", "object"
        },
        link_rel="noopener noreferrer",
        url_schemes={"http", "https"},
        url_relative=rewrite_relative,
    )
```

Do not add a second client-side HTML sanitizer; the template receives only this
cleaned fragment.

- [ ] **Step 4: Verify renderer GREEN**

Run: `python3 -m pytest tests/web/test_markdown.py -v`

Expected: table spans remain, relative images point at the task asset route, and every dangerous construct is absent.

- [ ] **Step 5: Commit safe Markdown rendering**

```bash
git add src/pdf_trans/web/markdown.py tests/web/test_markdown.py
git commit -m "feat: safely render translated markdown"
```

---

### Task 8: Reconnectable SSE Streams and Task APIs

**Files:**
- Create: `src/pdf_trans/web/streams.py`
- Create: `src/pdf_trans/web/routes/__init__.py`
- Create: `src/pdf_trans/web/routes/tasks.py`
- Create: `src/pdf_trans/web/routes/logs.py`
- Create: `src/pdf_trans/web/app.py`
- Create: `tests/web/test_streams.py`
- Create: `tests/web/test_task_routes.py`

**Interfaces:**
- Consumes: settings, repository, worker, safe upload.
- Produces: `create_app(settings=None, repository=None, worker=None) -> FastAPI`, upload/resume endpoints, task snapshot SSE, log history, and log SSE.

- [ ] **Step 1: Write failing stream generator tests**

Test the generators directly rather than leaving an infinite TestClient response:

```python
# tests/web/test_streams.py
import json

import pytest

from pdf_trans.web.streams import log_event_stream, task_event_stream


@pytest.mark.anyio
async def test_task_stream_sends_snapshot_then_stops(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    disconnects = iter([False, True])

    async def is_disconnected():
        return next(disconnects)

    async def no_sleep(seconds):
        return None

    stream = task_event_stream(
        repository,
        is_disconnected=is_disconnected,
        sleep=no_sleep,
    )
    event = await anext(stream)

    assert event.startswith("event: tasks\n")
    payload = json.loads(event.split("data: ", 1)[1])
    assert payload[0]["id"] == "a"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.anyio
async def test_log_stream_resumes_after_last_id(repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    first = repository.append_log("a", "INFO", "first")
    second = repository.append_log("a", "WARN", "second")
    async def connected():
        return False

    async def no_sleep(seconds):
        return None

    stream = log_event_stream(
        repository,
        task_id="a",
        after_id=first.id,
        is_disconnected=connected,
        sleep=no_sleep,
    )

    event = await anext(stream)

    assert f"id: {second.id}\n" in event
    assert '"message": "second"' in event
```

- [ ] **Step 2: Write failing upload/resume route tests**

Create a `FakeWorker` with `start`, `stop`, and `notify` counters, then:

```python
def test_upload_returns_202_and_notifies_worker(web_client, repository):
    response = web_client.post(
        "/tasks",
        files={"pdf": ("paper.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert repository.get_task(payload["id"]).original_filename == "paper.pdf"
    assert web_client.app.state.worker.notify_count == 1


def test_resume_requires_failed_or_interrupted_state(web_client, repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    assert web_client.post("/tasks/a/resume").status_code == 409
    repository.claim_next_task()
    repository.mark_failed("a", "timeout")
    response = web_client.post("/tasks/a/resume")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
```

Add these exact route tests:

```python
def test_upload_maps_validation_errors(web_client, monkeypatch):
    invalid = web_client.post(
        "/tasks",
        files={"pdf": ("paper.txt", b"no", "text/plain")},
    )
    assert invalid.status_code == 400

    async def too_large(*args, **kwargs):
        raise UploadValidationError("PDF 超过上传大小限制")

    monkeypatch.setattr(
        "pdf_trans.web.routes.tasks.save_pdf_upload",
        too_large,
    )
    oversized = web_client.post(
        "/tasks",
        files={"pdf": ("paper.pdf", b"%PDF-x", "application/pdf")},
    )
    assert oversized.status_code == 413


def test_missing_task_routes_return_404(web_client):
    assert web_client.post("/tasks/missing/resume").status_code == 404
    assert web_client.get("/tasks/missing/logs").status_code == 404


def test_log_history_uses_after_id(web_client, repository):
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    first = repository.append_log("a", "INFO", "first")
    repository.append_log("a", "INFO", "second")

    response = web_client.get(f"/tasks/a/logs?after_id={first.id}")

    assert response.status_code == 200
    assert [item["message"] for item in response.json()] == ["second"]
```

Import `UploadValidationError` from `pdf_trans.web.storage` in the route test.

Extend `tests/web/conftest.py` with:

```python
from fastapi.testclient import TestClient

from pdf_trans.web.app import create_app
from pdf_trans.web.config import WebSettings


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
```

- [ ] **Step 3: Run stream and route tests and verify RED**

Run: `python3 -m pytest tests/web/test_streams.py tests/web/test_task_routes.py -v`

Expected: imports fail for missing streams, routes, and app modules.

- [ ] **Step 4: Implement stable JSON serialization and SSE generators**

Create `src/pdf_trans/web/streams.py`:

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial

from anyio import to_thread

from pdf_trans.web.repository import LogView, TaskRepository, TaskView

POLL_SECONDS = 0.5
KEEPALIVE_SECONDS = 15.0


def task_to_dict(task: TaskView) -> dict[str, object]:
    return {
        "id": task.id,
        "original_filename": task.original_filename,
        "status": task.status,
        "attempt_count": task.attempt_count,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "started_at": (
            task.started_at.isoformat() if task.started_at else None
        ),
        "finished_at": (
            task.finished_at.isoformat() if task.finished_at else None
        ),
    }


def log_to_dict(log: LogView) -> dict[str, object]:
    return {
        "id": log.id,
        "task_id": log.task_id,
        "level": log.level,
        "message": log.message,
        "created_at": log.created_at.isoformat(),
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


async def task_event_stream(
    repository: TaskRepository,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    previous = ""
    idle = 0.0
    while not await is_disconnected():
        tasks = await to_thread.run_sync(repository.list_tasks)
        payload = _json([task_to_dict(task) for task in tasks])
        if payload != previous:
            previous = payload
            idle = 0.0
            yield f"event: tasks\ndata: {payload}\n\n"
            continue
        await sleep(POLL_SECONDS)
        idle += POLL_SECONDS
        if idle >= KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"


async def log_event_stream(
    repository: TaskRepository,
    *,
    task_id: str,
    after_id: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    cursor = after_id
    idle = 0.0
    while not await is_disconnected():
        logs = await to_thread.run_sync(
            partial(
                repository.list_logs,
                task_id,
                after_id=cursor,
                limit=500,
            )
        )
        if logs:
            idle = 0.0
            for log in logs:
                cursor = log.id
                yield (
                    f"id: {log.id}\n"
                    f"event: log\n"
                    f"data: {_json(log_to_dict(log))}\n\n"
                )
            continue
        await sleep(POLL_SECONDS)
        idle += POLL_SECONDS
        if idle >= KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"
```

The task stream emits only when the serialized snapshot changes. The log stream
uses the persisted integer primary key as its reconnect cursor.

- [ ] **Step 5: Implement task and log routers**

Create `src/pdf_trans/web/routes/tasks.py`:

```python
from __future__ import annotations

import shutil
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from pdf_trans.web.repository import InvalidTaskState, TaskNotFound
from pdf_trans.web.storage import (
    UploadValidationError,
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
```

Create `src/pdf_trans/web/routes/logs.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from pdf_trans.web.repository import TaskNotFound
from pdf_trans.web.streams import log_event_stream, log_to_dict

router = APIRouter()


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
```

Map repository exceptions exactly:

```text
TaskNotFound        -> HTTP 404
InvalidTaskState    -> HTTP 409
UploadValidationError containing "大小" -> HTTP 413
other UploadValidationError              -> HTTP 400
```

Read `Last-Event-ID` on the log stream, falling back to query parameter
`after_id=0`, as shown above.

- [ ] **Step 6: Implement the injectable application factory**

Create `src/pdf_trans/web/routes/__init__.py` as an empty package marker.
Create `src/pdf_trans/web/app.py`:

```python
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pdf_trans.web.config import WebSettings
from pdf_trans.web.db import (
    make_engine,
    make_session_factory,
    run_migrations,
)
from pdf_trans.web.repository import TaskRepository
from pdf_trans.web.routes.logs import router as logs_router
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
        else TaskLogHandler(repository)
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
    app.include_router(tasks_router)
    app.include_router(logs_router)
    return app
```

- [ ] **Step 7: Verify APIs GREEN**

Run: `python3 -m pytest tests/web/test_streams.py tests/web/test_task_routes.py -v`

Expected: snapshots, reconnect IDs, upload responses, status conflicts, and log pagination pass.

- [ ] **Step 8: Commit APIs and SSE**

```bash
git add src/pdf_trans/web tests/web
git commit -m "feat: expose task queue and log streams"
```

---

### Task 9: Server-Rendered Task Dashboard and Console Drawer

**Files:**
- Create: `src/pdf_trans/web/routes/pages.py`
- Create: `src/pdf_trans/web/templates/dashboard.html`
- Create: `src/pdf_trans/web/static/app.css`
- Create: `src/pdf_trans/web/static/dashboard.js`
- Modify: `src/pdf_trans/web/app.py`
- Create: `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `GET /tasks/events`, `GET /tasks/{id}/logs`, `GET /tasks/{id}/logs/events`, upload and resume endpoints.
- Produces: confirmed A-layout dashboard with upload zone, task rows, live state, and right Console drawer.

- [ ] **Step 1: Write failing dashboard page test**

```python
def test_dashboard_renders_confirmed_layout(web_client, repository):
    repository.create_task("a", "paper.pdf", "tasks/a/upload/source.pdf")

    response = web_client.get("/")

    assert response.status_code == 200
    assert 'id="upload-dropzone"' in response.text
    assert 'id="task-list"' in response.text
    assert 'id="console-drawer"' in response.text
    assert "paper.pdf" in response.text
    assert "/static/dashboard.js" in response.text


def test_dashboard_assets_define_responsive_drawer_and_status_styles(
    web_client,
):
    css = web_client.get("/static/app.css").text
    script = web_client.get("/static/dashboard.js").text

    assert "@media (max-width: 720px)" in css
    assert ".console-drawer" in css
    assert "new EventSource('/tasks/events')" in script
    assert "navigator.clipboard.writeText" in script
    assert "scrollHeight" in script
```

- [ ] **Step 2: Run page test and verify RED**

Run: `python3 -m pytest tests/web/test_pages.py -v`

Expected: `GET /` returns 404 or the page assets are missing.

- [ ] **Step 3: Build the dashboard template**

Create `src/pdf_trans/web/routes/pages.py`:

```python
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
            "max_upload_mib": (
                request.app.state.settings.max_upload_mib
            ),
        },
    )
```

Create `src/pdf_trans/web/templates/dashboard.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PDF Trans</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body class="dashboard-page">
  <header class="topbar">
    <strong>PDF Trans</strong>
    <span>MinerU · 单任务队列</span>
  </header>
  <main class="dashboard-shell">
    <form id="upload-form">
      <label id="upload-dropzone" class="upload-zone">
        <strong>拖放 PDF 到这里</strong>
        <span>或选择本地文件 · 最大 {{ max_upload_mib }} MiB</span>
        <span class="button">选择 PDF</span>
        <input id="pdf-input" name="pdf" type="file"
               accept=".pdf,application/pdf" hidden>
      </label>
      <p id="upload-error" class="error" role="alert"></p>
    </form>
    <section>
      <div class="section-heading">
        <h1>解析任务</h1>
        <span id="queue-summary"></span>
      </div>
      <ul id="task-list" class="task-list">
      {% for task in tasks %}
        <li class="task-row" data-task-id="{{ task.id }}"
            data-filename="{{ task.original_filename }}">
          <div>
            <strong>{{ task.original_filename }}</strong>
            <small>{{ task.created_at.isoformat() }} · 第 {{ task.attempt_count }} 次执行</small>
          </div>
          <span class="status status-{{ task.status }}">{{ task.status }}</span>
          <div class="task-actions">
            <button type="button" data-action="console">Console</button>
            {% if task.status in ("failed", "interrupted") %}
            <button type="button" data-action="resume">继续</button>
            {% endif %}
            {% if task.status == "succeeded" %}
            <a class="button primary" href="/tasks/{{ task.id }}/view"
               target="_blank" rel="noopener">查看</a>
            {% endif %}
          </div>
        </li>
      {% endfor %}
      </ul>
    </section>
  </main>
  <aside id="console-drawer" class="console-drawer"
         aria-hidden="true" aria-label="任务 Console">
    <header>
      <div><strong>Console</strong><span id="console-title"></span></div>
      <button id="console-close" type="button" aria-label="关闭">×</button>
    </header>
    <pre id="console-output" tabindex="0"></pre>
    <footer>
      <span id="console-connection">未连接</span>
      <label><input id="console-autoscroll" type="checkbox" checked> 自动滚动</label>
      <button id="console-copy" type="button">复制日志</button>
    </footer>
  </aside>
  <script src="/static/dashboard.js" defer></script>
</body>
</html>
```

Add `pages_router` to `create_app()` after the existing routers:

```python
from pdf_trans.web.routes.pages import router as pages_router

app.include_router(pages_router)
```

- [ ] **Step 4: Implement dashboard behavior**

Create `src/pdf_trans/web/static/dashboard.js`:

```javascript
const taskList = document.querySelector('#task-list');
const dropzone = document.querySelector('#upload-dropzone');
const fileInput = document.querySelector('#pdf-input');
const uploadError = document.querySelector('#upload-error');
const drawer = document.querySelector('#console-drawer');
const consoleOutput = document.querySelector('#console-output');
const consoleTitle = document.querySelector('#console-title');
const connection = document.querySelector('#console-connection');
const autoscroll = document.querySelector('#console-autoscroll');
let logSource = null;
let activeTaskId = null;
let activeFilename = '';
let logMessages = [];
let lastLogId = 0;

function actionButton(label, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.action = action;
  button.textContent = label;
  return button;
}

function renderTasks(tasks) {
  taskList.replaceChildren();
  let running = 0;
  let queued = 0;
  for (const task of tasks) {
    running += task.status === 'running' ? 1 : 0;
    queued += task.status === 'queued' ? 1 : 0;
    const row = document.createElement('li');
    row.className = 'task-row';
    row.dataset.taskId = task.id;
    row.dataset.filename = task.original_filename;
    const identity = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = task.original_filename;
    const detail = document.createElement('small');
    detail.textContent =
      `${new Date(task.created_at).toLocaleString()} · 第 ${task.attempt_count} 次执行`;
    identity.append(name, detail);
    const status = document.createElement('span');
    status.className = `status status-${task.status}`;
    status.textContent = task.status;
    const actions = document.createElement('div');
    actions.className = 'task-actions';
    actions.append(actionButton('Console', 'console'));
    if (['failed', 'interrupted'].includes(task.status)) {
      actions.append(actionButton('继续', 'resume'));
    }
    if (task.status === 'succeeded') {
      const view = document.createElement('a');
      view.className = 'button primary';
      view.href = `/tasks/${task.id}/view`;
      view.target = '_blank';
      view.rel = 'noopener';
      view.textContent = '查看';
      actions.append(view);
    }
    row.append(identity, status, actions);
    taskList.append(row);
  }
  document.querySelector('#queue-summary').textContent =
    `${running} 个运行中 · ${queued} 个等待中`;
}

function appendLog(log) {
  lastLogId = Math.max(lastLogId, log.id);
  logMessages.push(`[${log.level}] ${log.message}`);
  const line = document.createElement('span');
  line.className = `log-${log.level.toLowerCase()}`;
  line.textContent = `[${log.level}] ${log.message}\n`;
  consoleOutput.append(line);
  if (autoscroll.checked) {
    consoleOutput.scrollTop = consoleOutput.scrollHeight;
  }
}

async function loadHistory(taskId) {
  let cursor = 0;
  while (true) {
    const response = await fetch(`/tasks/${taskId}/logs?after_id=${cursor}`);
    if (!response.ok) throw new Error('无法读取历史日志');
    const logs = await response.json();
    logs.forEach(appendLog);
    if (logs.length < 500) return;
    cursor = logs[logs.length - 1].id;
  }
}

function connectLogs(taskId) {
  logSource = new EventSource(
    `/tasks/${taskId}/logs/events?after_id=${lastLogId}`
  );
  logSource.addEventListener('open', () => {
    connection.textContent = '● 实时连接';
  });
  logSource.addEventListener('log', (event) => {
    appendLog(JSON.parse(event.data));
  });
  logSource.addEventListener('error', () => {
    connection.textContent = '正在重连';
  });
}

async function openConsole(taskId, filename) {
  if (logSource) logSource.close();
  activeTaskId = taskId;
  activeFilename = filename;
  logMessages = [];
  lastLogId = 0;
  consoleOutput.replaceChildren();
  consoleTitle.textContent = filename;
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  connection.textContent = '正在加载';
  try {
    await loadHistory(taskId);
    connectLogs(taskId);
  } catch (error) {
    connection.textContent = error.message;
  }
}

function closeConsole() {
  if (logSource) logSource.close();
  logSource = null;
  activeTaskId = null;
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
}

async function resumeTask(taskId) {
  const response = await fetch(`/tasks/${taskId}/resume`, { method: 'POST' });
  if (!response.ok) {
    const payload = await response.json();
    uploadError.textContent = payload.detail || '无法继续任务';
  }
}

taskList.addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]');
  const row = event.target.closest('[data-task-id]');
  if (!action || !row) return;
  if (action.dataset.action === 'console') {
    openConsole(row.dataset.taskId, row.dataset.filename || '');
  } else if (action.dataset.action === 'resume') {
    resumeTask(row.dataset.taskId);
  }
});

async function upload(file) {
  uploadError.textContent = '';
  const body = new FormData();
  body.append('pdf', file);
  const response = await fetch('/tasks', { method: 'POST', body });
  if (!response.ok) {
    const payload = await response.json();
    uploadError.textContent = payload.detail || '上传失败';
  }
}

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) upload(fileInput.files[0]);
  fileInput.value = '';
});
for (const name of ['dragenter', 'dragover']) {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add('drag-active');
  });
}
for (const name of ['dragleave', 'drop']) {
  dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove('drag-active');
  });
}
dropzone.addEventListener('drop', (event) => {
  if (event.dataTransfer.files[0]) upload(event.dataTransfer.files[0]);
});

consoleOutput.addEventListener('scroll', () => {
  const distance = (
    consoleOutput.scrollHeight -
    consoleOutput.scrollTop -
    consoleOutput.clientHeight
  );
  if (distance > 32) autoscroll.checked = false;
});
document.querySelector('#console-close').addEventListener('click', closeConsole);
document.querySelector('#console-copy').addEventListener('click', () => {
  navigator.clipboard.writeText(logMessages.join('\n'));
});

const taskEvents = new EventSource('/tasks/events');
taskEvents.addEventListener('tasks', (event) => {
  renderTasks(JSON.parse(event.data));
});
```

- [ ] **Step 5: Add responsive visual styles**

Create `src/pdf_trans/web/static/app.css`:

```css
:root {
  color: #1f2937; background: #f3f5f8;
  font: 15px/1.5 system-ui, -apple-system, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; }
button, .button {
  border: 1px solid #cbd5e1; border-radius: 7px; background: white;
  color: #1f2937; padding: 7px 11px; font: inherit;
  text-decoration: none; cursor: pointer;
}
button:focus-visible, .button:focus-visible, input:focus-visible,
a:focus-visible { outline: 3px solid #93c5fd; outline-offset: 2px; }
.button.primary { background: #111827; border-color: #111827; color: white; }
.topbar {
  min-height: 58px; padding: 0 24px; background: #111827; color: white;
  display: flex; align-items: center; justify-content: space-between;
}
.topbar span { color: #94a3b8; font-size: 13px; }
.dashboard-shell { width: min(1040px, calc(100% - 32px)); margin: 28px auto; }
.upload-zone {
  min-height: 160px; border: 2px dashed #94a3b8; border-radius: 12px;
  background: white; display: flex; flex-direction: column; gap: 8px;
  align-items: center; justify-content: center; cursor: pointer;
}
.upload-zone.drag-active { border-color: #2563eb; background: #eff6ff; }
.upload-zone > span:not(.button) { color: #64748b; font-size: 13px; }
.error { min-height: 24px; color: #b91c1c; }
.section-heading { display: flex; align-items: center; justify-content: space-between; }
.section-heading h1 { font-size: 18px; }
.section-heading span, small { color: #64748b; }
.task-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.task-row {
  display: grid; grid-template-columns: minmax(0, 2fr) .7fr 1.2fr;
  gap: 16px; align-items: center; padding: 14px 16px;
  border: 1px solid #e2e8f0; border-radius: 10px; background: white;
}
.task-row strong, .task-row small { display: block; overflow-wrap: anywhere; }
.status { font-weight: 650; }
.status-queued { color: #64748b; }
.status-running { color: #2563eb; }
.status-succeeded { color: #15803d; }
.status-failed { color: #b91c1c; }
.status-interrupted { color: #b45309; }
.task-actions { display: flex; gap: 7px; justify-content: flex-end; flex-wrap: wrap; }
.console-drawer {
  position: fixed; inset: 0 0 0 auto; width: min(720px, 72vw);
  transform: translateX(100%); transition: transform .2s ease;
  background: #111827; color: #d1fae5; z-index: 20;
  display: grid; grid-template-rows: auto 1fr auto;
}
.console-drawer.open { transform: translateX(0); }
.console-drawer header, .console-drawer footer {
  padding: 12px 16px; display: flex; align-items: center;
  justify-content: space-between; gap: 12px; border-color: #334155;
}
.console-drawer header { border-bottom: 1px solid #334155; }
.console-drawer footer { border-top: 1px solid #334155; }
.console-drawer header span { display: block; color: #94a3b8; font-size: 12px; }
.console-drawer pre {
  overflow: auto; margin: 0; padding: 16px;
  font: 13px/1.65 ui-monospace, monospace; white-space: pre-wrap;
}
.log-info { color: #86efac; }
.log-warn, .log-warning { color: #fde047; }
.log-error, .log-critical { color: #fca5a5; }
@media (max-width: 720px) {
  .task-row { grid-template-columns: 1fr; }
  .task-actions { justify-content: flex-start; }
  .console-drawer { width: 100vw; }
}
```

Do not add inline styles to the template.

- [ ] **Step 6: Verify dashboard GREEN**

Run: `python3 -m pytest tests/web/test_pages.py -v`

Expected: the page, static resources, required controls, EventSource, copying,
and responsive rules are present.

- [ ] **Step 7: Commit dashboard**

```bash
git add src/pdf_trans/web/routes/pages.py src/pdf_trans/web/templates/dashboard.html src/pdf_trans/web/static tests/web/test_pages.py
git commit -m "feat: add web task dashboard and console"
```

---

### Task 10: Reader, Local KaTeX, Markdown Download, Assets, and Printing

**Files:**
- Create: `src/pdf_trans/web/routes/files.py`
- Create: `src/pdf_trans/web/templates/reader.html`
- Create: `src/pdf_trans/web/static/reader.js`
- Modify: `src/pdf_trans/web/static/app.css`
- Modify: `src/pdf_trans/web/app.py`
- Add: `src/pdf_trans/web/static/vendor/katex/katex.min.css`
- Add: `src/pdf_trans/web/static/vendor/katex/katex.min.js`
- Add: `src/pdf_trans/web/static/vendor/katex/auto-render.min.js`
- Add: `src/pdf_trans/web/static/vendor/katex/fonts/*`
- Add: `src/pdf_trans/web/static/vendor/katex/LICENSE.txt`
- Add: `src/pdf_trans/web/static/vendor/katex/SOURCE.txt`
- Create: `tests/web/test_file_routes.py`

**Interfaces:**
- Consumes: succeeded task `markdown_path`, safe Markdown renderer, safe asset resolution.
- Produces: new-tab reader, original Markdown download, same-origin images, local formula rendering, and browser PDF printing.

- [ ] **Step 1: Write failing reader/download/asset tests**

```python
def complete_task(repository, data_dir):
    repository.create_task("a", "paper.pdf", "tasks/a/upload/source.pdf")
    repository.claim_next_task()
    paper = data_dir / "tasks/a/attempts/1/paper"
    images = paper / "images"
    images.mkdir(parents=True)
    (images / "a.png").write_bytes(b"png")
    (paper / "normalized_content_list.json").write_text("[]")
    (paper / "rendered.md").write_text(
        "# 标题\n\n<table><tr><td>$x$</td></tr></table>\n\n"
        "![](images/a.png)\n\n<script>alert(1)</script>",
        encoding="utf-8",
    )
    repository.mark_succeeded(
        "a",
        normalized_path=(
            "tasks/a/attempts/1/paper/normalized_content_list.json"
        ),
        markdown_path="tasks/a/attempts/1/paper/rendered.md",
    )


def test_reader_is_sanitized_and_uses_local_katex(
    web_client, repository, web_settings
):
    complete_task(repository, web_settings.data_dir)
    response = web_client.get("/tasks/a/view")

    assert response.status_code == 200
    assert "<h1>标题</h1>" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "/static/vendor/katex/katex.min.js" in response.text
    assert "/static/vendor/katex/auto-render.min.js" in response.text
    assert 'id="print-button"' in response.text


def test_markdown_download_and_asset_route(
    web_client, repository, web_settings
):
    complete_task(repository, web_settings.data_dir)

    markdown = web_client.get("/tasks/a/markdown")
    image = web_client.get("/tasks/a/assets/images/a.png")

    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "attachment;" in markdown.headers["content-disposition"]
    assert image.content == b"png"
    assert web_client.get("/tasks/a/assets/../../upload/source.pdf").status_code in {
        404, 400
    }


def test_unfinished_task_cannot_open_reader(web_client, repository):
    repository.create_task("a", "paper.pdf", "tasks/a/upload/source.pdf")
    assert web_client.get("/tasks/a/view").status_code == 409
    assert web_client.get("/tasks/a/markdown").status_code == 409
```

- [ ] **Step 2: Run reader tests and verify RED**

Run: `python3 -m pytest tests/web/test_file_routes.py -v`

Expected: reader/download routes return 404 and KaTeX assets are absent.

- [ ] **Step 3: Vendor and verify KaTeX 0.18.1**

Download only from:

```text
https://registry.npmjs.org/katex/-/katex-0.18.1.tgz
```

Verify npm integrity before extraction:

```text
sha512-Td8GCYSxDAoMhHOlKmCFMJ/hz5qlAAb71n66Dryw9nfCVfumLo7nhuotbvKom/XPADmrYC3O5QR71EPq4DarJQ==
```

Copy `dist/katex.min.css`, `dist/katex.min.js`,
`dist/contrib/auto-render.min.js`, every referenced `dist/fonts/*` file, and
`LICENSE` into the package paths listed above. Write `SOURCE.txt` containing
the version, URL, and integrity line. Do not add npm metadata, source maps,
Node modules, or a package lock.

Use this mechanical extraction sequence:

```bash
katex_tmp_dir=$(mktemp -d)
curl --fail --location --silent --show-error \
  https://registry.npmjs.org/katex/-/katex-0.18.1.tgz \
  --output "$katex_tmp_dir/katex.tgz"
katex_actual_integrity=$(openssl dgst -sha512 -binary \
  "$katex_tmp_dir/katex.tgz" | openssl base64 -A)
test "$katex_actual_integrity" = \
  "Td8GCYSxDAoMhHOlKmCFMJ/hz5qlAAb71n66Dryw9nfCVfumLo7nhuotbvKom/XPADmrYC3O5QR71EPq4DarJQ=="
tar -xzf "$katex_tmp_dir/katex.tgz" -C "$katex_tmp_dir"
mkdir -p src/pdf_trans/web/static/vendor/katex/fonts
cp "$katex_tmp_dir/package/dist/katex.min.css" \
  src/pdf_trans/web/static/vendor/katex/katex.min.css
cp "$katex_tmp_dir/package/dist/katex.min.js" \
  src/pdf_trans/web/static/vendor/katex/katex.min.js
cp "$katex_tmp_dir/package/dist/contrib/auto-render.min.js" \
  src/pdf_trans/web/static/vendor/katex/auto-render.min.js
cp "$katex_tmp_dir/package/dist/fonts/"* \
  src/pdf_trans/web/static/vendor/katex/fonts/
cp "$katex_tmp_dir/package/LICENSE" \
  src/pdf_trans/web/static/vendor/katex/LICENSE.txt
```

Create `SOURCE.txt` with `apply_patch`; do not use a heredoc:

```text
KaTeX 0.18.1
Source: https://registry.npmjs.org/katex/-/katex-0.18.1.tgz
Integrity: sha512-Td8GCYSxDAoMhHOlKmCFMJ/hz5qlAAb71n66Dryw9nfCVfumLo7nhuotbvKom/XPADmrYC3O5QR71EPq4DarJQ==
```

- [ ] **Step 4: Implement file routes and reader page**

Create `src/pdf_trans/web/routes/files.py`:

```python
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
```

Create `src/pdf_trans/web/templates/reader.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ task.original_filename }} · PDF Trans</title>
  <link rel="stylesheet" href="/static/app.css">
  <link rel="stylesheet" href="/static/vendor/katex/katex.min.css">
  <script src="/static/vendor/katex/katex.min.js" defer></script>
  <script src="/static/vendor/katex/auto-render.min.js" defer></script>
  <script src="/static/reader.js" defer></script>
</head>
<body class="reader-page">
  <header class="reader-toolbar">
    <div>
      <strong>{{ task.original_filename }}</strong>
      <small>由 PDF Trans 生成 · rendered.md</small>
    </div>
    <nav>
      <a class="button" href="/tasks/{{ task.id }}/markdown">下载 Markdown</a>
      <button id="print-button" type="button" disabled>打印 / 导出 PDF</button>
    </nav>
  </header>
  <article id="reader-article" class="reader-article">{{ content }}</article>
</body>
</html>
```

Import and include `files_router` in `create_app()` after `pages_router`.

- [ ] **Step 5: Implement KaTeX and print behavior**

Create `src/pdf_trans/web/static/reader.js`:

```javascript
document.addEventListener('DOMContentLoaded', () => {
  const article = document.querySelector('#reader-article');
  const printButton = document.querySelector('#print-button');
  renderMathInElement(article, {
    delimiters: [
      { left: '$$', right: '$$', display: true },
      { left: '$', right: '$', display: false },
      { left: '\\(', right: '\\)', display: false },
      { left: '\\[', right: '\\]', display: true }
    ],
    throwOnError: false,
    trust: false,
    maxSize: 20,
    maxExpand: 500
  });
  printButton.disabled = false;
  printButton.addEventListener('click', () => window.print());
});
```

Append to `app.css`:

```css
.reader-page { background: #eef2f7; }
.reader-toolbar {
  position: sticky; top: 0; z-index: 5; padding: 10px 20px;
  border-bottom: 1px solid #e2e8f0; background: white;
  display: flex; justify-content: space-between; align-items: center; gap: 16px;
}
.reader-toolbar strong, .reader-toolbar small { display: block; }
.reader-toolbar nav { display: flex; gap: 8px; }
.reader-article {
  width: min(860px, calc(100% - 32px)); margin: 24px auto;
  padding: 48px 56px; background: white; box-shadow: 0 8px 30px #0f172a18;
  font: 17px/1.75 Georgia, "Noto Serif CJK SC", serif;
}
.reader-article img { display: block; max-width: 100%; height: auto; margin: 20px auto; }
.reader-article table {
  display: block; max-width: 100%; overflow-x: auto;
  border-collapse: collapse; font-family: system-ui, sans-serif;
}
.reader-article th, .reader-article td {
  border: 1px solid #94a3b8; padding: 7px 9px; text-align: left;
}
.reader-article th { background: #f1f5f9; }
.reader-article pre { overflow-x: auto; padding: 14px; background: #f8fafc; }
@media print {
  @page { size: A4; margin: 16mm; }
  .reader-toolbar { display: none !important; }
  .reader-page { background: white; padding: 0; }
  .reader-article { max-width: none; box-shadow: none; }
  .reader-article table {
    display: table;
    overflow: visible;
    width: 100%;
    font-size: 9pt;
  }
  .reader-article h1,
  .reader-article h2,
  .reader-article h3,
  .reader-article img,
  .reader-article table,
  .reader-article .katex-display {
    break-inside: avoid;
  }
}
@media (max-width: 720px) {
  .reader-toolbar { align-items: flex-start; flex-direction: column; }
  .reader-article { width: 100%; margin: 0; padding: 24px 18px; box-shadow: none; }
}
```

- [ ] **Step 6: Verify reader GREEN**

Run: `python3 -m pytest tests/web/test_markdown.py tests/web/test_file_routes.py tests/web/test_pages.py -v`

Expected: safe HTML, local formula resources, downloads, assets, print controls, wide-table CSS, and state conflicts pass.

- [ ] **Step 7: Commit reader and vendored assets**

```bash
git add src/pdf_trans/web tests/web/test_file_routes.py
git commit -m "feat: preview download and print translated markdown"
```

---

### Task 11: Production Entrypoint, Documentation, Packaging, and Full Verification

**Files:**
- Create: `src/pdf_trans/web/__main__.py`
- Modify: `README.md`
- Create: `tests/web/test_web_entrypoint.py`

**Interfaces:**
- Consumes: `WebSettings.from_env()` and `create_app()`.
- Produces: `python3 -m pdf_trans.web`, installation documentation, MySQL example, and distributable templates/static/migrations.

- [ ] **Step 1: Write failing entrypoint and package-resource tests**

```python
# tests/web/test_web_entrypoint.py
from importlib.resources import files

from pdf_trans.web import __main__ as web_main


def test_main_runs_single_uvicorn_process(monkeypatch):
    received = {}
    monkeypatch.setattr(
        web_main,
        "create_app",
        lambda settings: "application",
    )
    monkeypatch.setattr(
        web_main.uvicorn,
        "run",
        lambda app, **kwargs: received.update(app=app, **kwargs),
    )

    assert web_main.main(environ={
        "PDF_TRANS_WEB_HOST": "0.0.0.0",
        "PDF_TRANS_WEB_PORT": "8123",
    }) == 0
    assert received == {
        "app": "application",
        "host": "0.0.0.0",
        "port": 8123,
        "workers": 1,
    }


def test_web_package_contains_runtime_resources():
    package = files("pdf_trans.web")
    assert package.joinpath("templates/dashboard.html").is_file()
    assert package.joinpath("templates/reader.html").is_file()
    assert package.joinpath("static/dashboard.js").is_file()
    assert package.joinpath("static/vendor/katex/katex.min.js").is_file()
    assert package.joinpath(
        "migrations/versions/0001_create_web_tasks.py"
    ).is_file()
```

- [ ] **Step 2: Run entrypoint tests and verify RED**

Run: `python3 -m pytest tests/web/test_web_entrypoint.py -v`

Expected: import fails because `pdf_trans.web.__main__` does not exist.

- [ ] **Step 3: Implement the single-worker launcher**

Create `src/pdf_trans/web/__main__.py`:

```python
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

import uvicorn

from pdf_trans.web.app import create_app
from pdf_trans.web.config import WebSettings


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    del argv
    settings = WebSettings.from_env(
        environ=os.environ if environ is None else environ
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Update README with exact operations**

Document:

```bash
python3 -m pip install -e '.[web]'
python3 -m pdf_trans.web
```

Explain one process/one queued task, the five statuses, startup interruption,
Continue semantics, Console behavior, local Markdown/HTML/KaTeX rendering,
Markdown download, and browser “Print / Save as PDF”.

List all six `PDF_TRANS_*` Web variables and existing translation variables.
Include:

```bash
export PDF_TRANS_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1/pdf_trans?charset=utf8mb4'
python3 -m pip install pymysql
```

State that the MySQL database must already exist and that startup runs Alembic
migrations. Move every completed Web preview item out of “待办事项”; leave only
genuinely unimplemented work.

- [ ] **Step 5: Verify installed resource packaging**

Run:

```bash
python3 -m build
unzip -l dist/pdf_trans-0.1.0-py3-none-any.whl | \
  rg 'templates/dashboard.html|static/vendor/katex/fonts|0001_create_web_tasks.py'
python3 -m pip install --force-reinstall --no-deps dist/pdf_trans-0.1.0-py3-none-any.whl
python3 -m pytest tests/web/test_web_entrypoint.py -v
python3 -m pip install -e '.[test,web]'
```

Expected: the archive listing matches all three resource categories, and the
entrypoint tests pass.

- [ ] **Step 6: Run the complete automated suite**

Run: `python3 -m pytest -v`

Expected: all original 130 tests and all new Web tests pass with no warnings from application code.

- [ ] **Step 7: Run a local smoke test with fakes**

Start the app against a temporary SQLite database and a fake injected runner,
upload a tiny `%PDF-` fixture, and verify in a browser:

1. upload returns immediately and the task becomes queued;
2. only one task becomes running;
3. Console receives persisted logs;
4. completed task opens the reader in a new tab;
5. table spans and formula render;
6. image loads through the task asset route;
7. Markdown downloads;
8. Print opens the browser dialog with toolbar hidden in preview;
9. restarting the app changes an inserted running row to interrupted;
10. Continue resumes the same task ID and preserves old logs.

Do not call the real MinerU or translation API during this smoke test.

- [ ] **Step 8: Inspect branch-only changes**

Run:

```bash
git status --short
git diff --check
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
```

Expected: only planned Web files, tests, README, pyproject, design, and plan
changes appear; `main` remains at `90ee25e`.

- [ ] **Step 9: Commit documentation and entrypoint**

```bash
git add README.md src/pdf_trans/web/__main__.py tests
git commit -m "docs: document web task dashboard"
```

- [ ] **Step 10: Request final code review**

Invoke `superpowers:requesting-code-review`, address findings with
`superpowers:receiving-code-review`, then invoke
`superpowers:verification-before-completion` and rerun the complete suite before
claiming implementation success.
