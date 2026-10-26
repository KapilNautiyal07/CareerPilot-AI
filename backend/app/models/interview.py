"""Mock interviews and assessment history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow
from app.models.enums import AssessmentSource, InterviewCategory, InterviewStatus, QuestionKind, db_enum


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (Index("ix_interview_sessions_profile_started", "profile_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    category: Mapped[InterviewCategory] = mapped_column(db_enum(InterviewCategory, "interview_category"))
    target_role: Mapped[str] = mapped_column(String(120), default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[InterviewStatus] = mapped_column(
        db_enum(InterviewStatus, "interview_status"), default=InterviewStatus.in_progress
    )
    max_questions: Mapped[int] = mapped_column(Integer, default=5)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    overall_score: Mapped[float | None] = mapped_column(Float)  # 0-100
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    questions: Mapped[list[InterviewQuestion]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InterviewQuestion.order_index",
    )


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True)
    parent_question_id: Mapped[int | None] = mapped_column(ForeignKey("interview_questions.id", ondelete="CASCADE"))
    bank_id: Mapped[str] = mapped_column(String(80))
    order_index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[QuestionKind] = mapped_column(db_enum(QuestionKind, "question_kind"))
    category: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    rubric: Mapped[list[dict]] = mapped_column(JSON, default=list)
    hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    code_spec: Mapped[dict | None] = mapped_column(JSON)
    hints_revealed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[InterviewSession] = relationship(back_populates="questions")
    response: Mapped[InterviewResponse | None] = relationship(
        back_populates="question", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class InterviewResponse(Base):
    __tablename__ = "interview_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("interview_questions.id", ondelete="CASCADE"), unique=True)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)  # 0-100
    criteria_results: Mapped[list[dict]] = mapped_column(JSON, default=list)
    feedback: Mapped[str] = mapped_column(Text, default="")
    mistakes: Mapped[list[str]] = mapped_column(JSON, default=list)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    evaluator: Mapped[str] = mapped_column(String(40))
    code_execution: Mapped[dict | None] = mapped_column(JSON)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    question: Mapped[InterviewQuestion] = relationship(back_populates="response")


class AssessmentResult(Base):
    """Append-only history of measured performance (per skill or overall readiness)."""

    __tablename__ = "assessment_results"
    __table_args__ = (Index("ix_assessment_results_profile_skill_created", "profile_id", "skill", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"))
    source: Mapped[AssessmentSource] = mapped_column(db_enum(AssessmentSource, "assessment_source"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("interview_sessions.id", ondelete="SET NULL"))
    skill: Mapped[str | None] = mapped_column(String(80))
    competency: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(Float)  # 0-100
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
