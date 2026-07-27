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
