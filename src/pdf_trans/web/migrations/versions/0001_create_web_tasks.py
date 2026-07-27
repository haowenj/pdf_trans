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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
