"""Job catalog (shared across users), per-profile matches and applications."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, utcnow
from app.db.types import EmbeddingVector
from app.models.enums import ApplicationStatus, RequirementLevel, Seniority, WorkArrangement, db_enum

EMBED_DIM = get_settings().embedding_dim


class JobListing(TimestampMixin, Base):
    """A normalized job posting with provenance. Listings are a shared cache, not user data."""

    __tablename__ = "job_listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index("ix_job_listings_title_company", "title", "company"),
        Index(
            "ix_job_listings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)  # mock | greenhouse | lever
    external_id: Mapped[str] = mapped_column(String(200))
    # Cross-source dedupe key: normalized company + title + location.
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200), default="")
    work_arrangement: Mapped[WorkArrangement] = mapped_column(
        db_enum(WorkArrangement, "work_arrangement"), default=WorkArrangement.unknown
    )
    employment_type: Mapped[str] = mapped_column(String(40), default="unknown")
    seniority: Mapped[Seniority] = mapped_column(db_enum(Seniority, "job_seniority"), default=Seniority.unknown)
    min_years_experience: Mapped[float | None] = mapped_column(Float)
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(Text, default="")
    application_url: Mapped[str | None] = mapped_column(String(1000))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector(EMBED_DIM))

    requirements: Mapped[list[JobSkillRequirement]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class JobSkillRequirement(Base):
    __tablename__ = "job_skill_requirements"
    __table_args__ = (UniqueConstraint("job_id", "skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_listings.id", ondelete="CASCADE"), index=True)
    skill: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[RequirementLevel] = mapped_column(db_enum(RequirementLevel, "requirement_level"))
    evidence_text: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[JobListing] = relationship(back_populates="requirements")


class JobMatch(Base):
    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint("profile_id", "job_id"),
        Index("ix_job_matches_profile_score", "profile_id", "score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey("job_listings.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)  # 0-100 overall rank score
    skill_score: Mapped[float] = mapped_column(Float)
    experience_score: Mapped[float] = mapped_column(Float)
    preference_score: Mapped[float] = mapped_column(Float)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[JobListing] = relationship()


class JobApplication(TimestampMixin, Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint("profile_id", "job_id"),
        Index("ix_job_applications_profile_status", "profile_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_listings.id", ondelete="SET NULL"))
    company: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    job_url: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[ApplicationStatus] = mapped_column(
        db_enum(ApplicationStatus, "application_status"), default=ApplicationStatus.saved
    )
    applied_on: Mapped[date | None] = mapped_column(Date)
    next_step: Mapped[str] = mapped_column(String(300), default="")
    next_step_due: Mapped[date | None] = mapped_column(Date, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    status_history: Mapped[list[dict]] = mapped_column(JSON, default=list)

    job: Mapped[JobListing | None] = relationship()
