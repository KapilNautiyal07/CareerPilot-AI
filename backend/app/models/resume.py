"""Resumes and the structured profile data extracted from them."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import EvidenceLevel, ResumeStatus, SkillSource, db_enum

if TYPE_CHECKING:
    from app.models.user import CandidateProfile


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (UniqueConstraint("profile_id", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # Personal data: never logged, purged by the retention job.
    raw_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ResumeStatus] = mapped_column(db_enum(ResumeStatus, "resume_status"), default=ResumeStatus.uploaded)
    analysis: Mapped[dict | None] = mapped_column(JSON)
    security_flags: Mapped[list[dict]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[CandidateProfile] = relationship(back_populates="resumes", foreign_keys=[profile_id])


class ExtractedSkill(TimestampMixin, Base):
    __tablename__ = "extracted_skills"
    __table_args__ = (UniqueConstraint("profile_id", "skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    skill: Mapped[str] = mapped_column(String(80))  # canonical taxonomy name
    category: Mapped[str] = mapped_column(String(40), default="other")
    source: Mapped[SkillSource] = mapped_column(db_enum(SkillSource, "skill_source"))
    evidence_level: Mapped[EvidenceLevel] = mapped_column(db_enum(EvidenceLevel, "evidence_level"))
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    years: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    profile: Mapped[CandidateProfile] = relationship(back_populates="skills")


class WorkExperience(TimestampMixin, Base):
    __tablename__ = "work_experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[list[str]] = mapped_column(JSON, default=list)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    profile: Mapped[CandidateProfile] = relationship(back_populates="experiences")


class ProjectExperience(TimestampMixin, Base):
    __tablename__ = "project_experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    technologies: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str | None] = mapped_column(String(500))
    highlights: Mapped[list[str]] = mapped_column(JSON, default=list)

    profile: Mapped[CandidateProfile] = relationship(back_populates="projects")
