"""Agent workflow executions (status, steps and a result summary; graph state lives in the checkpointer)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow
from app.models.enums import WorkflowKind, WorkflowStatus, db_enum


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (Index("ix_workflow_executions_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[WorkflowKind] = mapped_column(db_enum(WorkflowKind, "workflow_kind"))
    status: Mapped[WorkflowStatus] = mapped_column(
        db_enum(WorkflowStatus, "workflow_status"), default=WorkflowStatus.queued
    )
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    current_node: Mapped[str | None] = mapped_column(String(60))
    steps: Mapped[list[dict]] = mapped_column(JSON, default=list)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    pending_question: Mapped[dict | None] = mapped_column(JSON)  # set while awaiting_input
    error: Mapped[dict | None] = mapped_column(JSON)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
