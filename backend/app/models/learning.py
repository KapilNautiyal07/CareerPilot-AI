"""Skill gaps, roadmaps, tasks and learning resources."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, utcnow
from app.db.types import EmbeddingVector
from app.models.enums import (
    GapStatus,
    RequirementLevel,
    ResourceVerification,
    RoadmapStatus,
    TaskStatus,
    TaskType,
    db_enum,
)

EMBED_DIM = get_settings().embedding_dim


class SkillGap(Base):
    """One row per skill per analysis snapshot. `is_current` marks the latest snapshot;
    older snapshots are kept to chart improvement over time."""

    __tablename__ = "skill_gaps"
    __table_args__ = (
        Index("ix_skill_gaps_profile_current", "profile_id", "is_current"),
        Index("ix_skill_gaps_snapshot", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    snapshot_id: Mapped[str] = mapped_column(String(36))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    target_role: Mapped[str] = mapped_column(String(120), default="")
    skill: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40), default="other")
    status: Mapped[GapStatus] = mapped_column(db_enum(GapStatus, "gap_status"))
    requirement_level: Mapped[RequirementLevel] = mapped_column(db_enum(RequirementLevel, "gap_requirement_level"))
    demand_share: Mapped[float] = mapped_column(Float, default=0.0)  # share of target jobs asking for it
    evidence_strength: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    priority_components: Mapped[dict] = mapped_column(JSON, default=dict)
    estimated_hours: Mapped[float] = mapped_column(Float, default=0.0)
    transferable_from: Mapped[list[str]] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


roadmap_task_resources = Table(
    "roadmap_task_resources",
    Base.metadata,
    Column("task_id", ForeignKey("roadmap_tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("resource_id", ForeignKey("learning_resources.id", ondelete="CASCADE"), primary_key=True),
)


class LearningResource(TimestampMixin, Base):
    __tablename__ = "learning_resources"
    __table_args__ = (
        Index(
            "ix_learning_resources_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    provider: Mapped[str] = mapped_column(String(120), default="")
    resource_type: Mapped[str] = mapped_column(String(40), default="docs")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    level: Mapped[str] = mapped_column(String(20), default="all")
    estimated_hours: Mapped[float | None] = mapped_column(Float)
    is_free: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Only set to `reachable` by an actual HTTP check; never assumed.
    verification_status: Mapped[ResourceVerification] = mapped_column(
        db_enum(ResourceVerification, "resource_verification"), default=ResourceVerification.unverified
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector(EMBED_DIM))


class LearningRoadmap(TimestampMixin, Base):
    __tablename__ = "learning_roadmaps"
    __table_args__ = (Index("ix_learning_roadmaps_profile_status", "profile_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    workflow_execution_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_executions.id", ondelete="SET NULL"))
    status: Mapped[RoadmapStatus] = mapped_column(
        db_enum(RoadmapStatus, "roadmap_status"), default=RoadmapStatus.active
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    target_role: Mapped[str] = mapped_column(String(120), default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    weekly_hours: Mapped[float] = mapped_column(Float)
    study_days_per_week: Mapped[int] = mapped_column(Integer, default=5)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # coverage, uncovered gaps, evaluation
    revision_log: Mapped[list[dict]] = mapped_column(JSON, default=list)

    tasks: Mapped[list[RoadmapTask]] = relationship(
        back_populates="roadmap",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(RoadmapTask.scheduled_date, RoadmapTask.order_index)",
    )


class RoadmapTask(TimestampMixin, Base):
    __tablename__ = "roadmap_tasks"
    __table_args__ = (Index("ix_roadmap_tasks_roadmap_status", "roadmap_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    roadmap_id: Mapped[int] = mapped_column(ForeignKey("learning_roadmaps.id", ondelete="CASCADE"))
    week_index: Mapped[int] = mapped_column(Integer)
    scheduled_date: Mapped[date] = mapped_column(Date, index=True)
    due_date: Mapped[date] = mapped_column(Date)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[TaskType] = mapped_column(db_enum(TaskType, "task_type"))
    skill: Mapped[str | None] = mapped_column(String(80))
    estimated_hours: Mapped[float] = mapped_column(Float)
    status: Mapped[TaskStatus] = mapped_column(db_enum(TaskStatus, "task_status"), default=TaskStatus.pending)
    origin: Mapped[str] = mapped_column(String(20), default="generated")  # generated | replanned | remediation
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    self_rating: Mapped[int | None] = mapped_column(Integer)  # 1-5, optional user reflection
    notes: Mapped[str] = mapped_column(Text, default="")

    roadmap: Mapped[LearningRoadmap] = relationship(back_populates="tasks")
    resources: Mapped[list[LearningResource]] = relationship(secondary=roadmap_task_resources)
