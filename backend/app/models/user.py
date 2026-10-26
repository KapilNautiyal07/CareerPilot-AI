"""Users, preferences and candidate profiles."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import Seniority, db_enum

if TYPE_CHECKING:
    from app.models.resume import ExtractedSkill, ProjectExperience, Resume, WorkExperience


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    # Incremented to invalidate every issued token (logout everywhere, password change).
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[CandidateProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    preference: Mapped[UserPreference | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class UserPreference(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    weekly_study_hours: Mapped[float] = mapped_column(Float, default=8.0)
    study_days_per_week: Mapped[int] = mapped_column(Integer, default=5)
    target_deadline: Mapped[date | None] = mapped_column(Date)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    work_arrangements: Mapped[list[str]] = mapped_column(JSON, default=list)
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str] = mapped_column(String(3), default="USD")
    interview_categories: Mapped[list[str]] = mapped_column(JSON, default=list)

    user: Mapped[User] = relationship(back_populates="preference")


class CandidateProfile(TimestampMixin, Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    headline: Mapped[str] = mapped_column(String(200), default="")
    target_role: Mapped[str] = mapped_column(String(120), default="")
    target_seniority: Mapped[Seniority] = mapped_column(db_enum(Seniority, "seniority"), default=Seniority.unknown)
    # User-confirmed value wins over the extracted one when both exist.
    years_experience: Mapped[float | None] = mapped_column(Float)
    location: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    education: Mapped[list[dict]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[dict]] = mapped_column(JSON, default=list)
    achievements: Mapped[list[str]] = mapped_column(JSON, default=list)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    active_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL", use_alter=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="profile")
    resumes: Mapped[list[Resume]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="Resume.profile_id",
    )
    skills: Mapped[list[ExtractedSkill]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
    experiences: Mapped[list[WorkExperience]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkExperience.order_index",
    )
    projects: Mapped[list[ProjectExperience]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )
