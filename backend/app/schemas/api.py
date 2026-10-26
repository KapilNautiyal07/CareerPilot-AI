"""Request/response schemas for the REST API."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    ApplicationStatus,
    EvidenceLevel,
    InterviewCategory,
    Seniority,
    TaskStatus,
    WorkArrangement,
)

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=72)
    full_name: str = Field(default="", max_length=200)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("Password must contain letters and numbers")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    is_demo: bool
    created_at: datetime
    onboarding_completed: bool = False


class AuthOut(BaseModel):
    user: UserOut
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret


# ---------------------------------------------------------------------- profile
class PreferencesIn(BaseModel):
    weekly_study_hours: float = Field(ge=0.5, le=80)
    study_days_per_week: int = Field(ge=1, le=7)
    target_deadline: date | None = None
    preferred_locations: list[str] = Field(default_factory=list, max_length=10)
    work_arrangements: list[WorkArrangement] = Field(default_factory=list)
    salary_min: int | None = Field(default=None, ge=0)
    salary_currency: str = Field(default="USD", min_length=3, max_length=3)
    interview_categories: list[InterviewCategory] = Field(default_factory=list)

    @field_validator("target_deadline")
    @classmethod
    def _future(cls, v: date | None) -> date | None:
        if v is not None and v <= date.today():
            raise ValueError("Deadline must be in the future")
        return v


class PreferencesOut(ORM):
    weekly_study_hours: float
    study_days_per_week: int
    target_deadline: date | None
    preferred_locations: list[str]
    work_arrangements: list[str]
    salary_min: int | None
    salary_currency: str
    interview_categories: list[str]


class ProfileIn(BaseModel):
    headline: str = Field(default="", max_length=200)
    target_role: str = Field(default="", max_length=120)
    target_seniority: Seniority = Seniority.unknown
    years_experience: float | None = Field(default=None, ge=0, le=60)
    location: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=4000)


class OnboardingIn(BaseModel):
    profile: ProfileIn
    preferences: PreferencesIn


class SkillOut(ORM):
    id: int
    skill: str
    category: str
    source: str
    evidence_level: str
    evidence: list[str]
    years: float | None
    confidence: float
    user_confirmed: bool


class SkillIn(BaseModel):
    skill: str = Field(min_length=1, max_length=80)
    evidence: str = Field(default="", max_length=500)


class SkillPatch(BaseModel):
    user_confirmed: bool | None = None
    evidence_level: Literal["demonstrated", "listed", "user_asserted"] | None = None
    evidence: list[str] | None = Field(default=None, max_length=10)


class ExperienceOut(ORM):
    id: int
    title: str
    company: str
    location: str
    start_date: date | None
    end_date: date | None
    is_current: bool
    highlights: list[str]
    skills: list[str]


class ExperiencePatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    highlights: list[str] | None = Field(default=None, max_length=30)


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    technologies: list[str]
    url: str | None
    highlights: list[str]


class ProfileOut(ORM):
    id: int
    headline: str
    target_role: str
    target_seniority: str
    years_experience: float | None
    location: str
    summary: str
    education: list[dict]
    certifications: list[dict]
    achievements: list[str]
    onboarding_completed: bool
    active_resume_id: int | None
    skills: list[SkillOut]
    experiences: list[ExperienceOut]
    projects: list[ProjectOut]
    preferences: PreferencesOut | None = None


# ---------------------------------------------------------------------- resumes
class ResumeOut(ORM):
    id: int
    original_filename: str
    content_type: str
    size_bytes: int
    status: str
    uploaded_at: datetime
    analyzed_at: datetime | None
    security_flags: list[dict]
    error: str | None
    is_active: bool = False
    analysis: dict | None = None


class CompareIn(BaseModel):
    job_id: int | None = None
    job_description: str | None = Field(default=None, max_length=30_000)
    job_title: str = Field(default="Pasted job description", max_length=200)


# ---------------------------------------------------------------------- jobs
class JobMatchOut(BaseModel):
    score: float
    skill_score: float
    experience_score: float
    preference_score: float
    details: list[dict]
    notes: list[str]
    computed_at: datetime


class JobOut(BaseModel):
    id: int
    source: str
    title: str
    company: str
    location: str
    work_arrangement: str
    employment_type: str
    seniority: str
    min_years_experience: float | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    application_url: str | None
    source_url: str | None
    posted_at: datetime | None
    retrieved_at: datetime
    last_checked_at: datetime
    is_demo: bool
    quality_flags: list[str]
    required_skills: list[str]
    preferred_skills: list[str]
    is_saved: bool = False
    application_status: str | None = None
    match: JobMatchOut | None = None


class JobDetailOut(JobOut):
    description: str
    requirements: list[dict]
    tailoring: dict | None = None
    availability_note: str


# ---------------------------------------------------------------------- roadmap
class TaskOut(ORM):
    id: int
    week_index: int
    scheduled_date: date
    due_date: date
    title: str
    description: str
    task_type: str
    skill: str | None
    estimated_hours: float
    status: str
    origin: str
    completed_at: datetime | None
    self_rating: int | None
    notes: str
    resources: list[dict] = Field(default_factory=list)
    overdue: bool = False


class TaskPatch(BaseModel):
    status: Literal[TaskStatus.pending, TaskStatus.completed, TaskStatus.skipped] | None = None
    self_rating: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=2000)


class RoadmapOut(BaseModel):
    id: int
    version: int
    title: str
    target_role: str
    start_date: date
    end_date: date
    weekly_hours: float
    study_days_per_week: int
    summary: dict
    revision_log: list[dict]
    progress: dict
    weeks: list[dict]


# ---------------------------------------------------------------------- interviews
class InterviewStartIn(BaseModel):
    category: InterviewCategory = InterviewCategory.mixed
    target_role: str | None = Field(default=None, max_length=120)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    num_questions: int = Field(default=4, ge=1, le=8)


class AnswerIn(BaseModel):
    answer: str = Field(default="", max_length=20_000)
    code: str | None = Field(default=None, max_length=20_000)


class QuestionOut(BaseModel):
    id: int
    order_index: int
    kind: str
    category: str
    prompt: str
    skills: list[str]
    is_follow_up: bool
    hints_available: int
    hints_revealed: list[str]
    code_function: str | None = None
    response: dict | None = None


class SessionOut(BaseModel):
    id: int
    category: str
    target_role: str
    difficulty: str
    status: str
    max_questions: int
    overall_score: float | None
    summary: dict
    started_at: datetime
    completed_at: datetime | None
    questions: list[QuestionOut]
    current_question: QuestionOut | None


class AnswerOut(BaseModel):
    evaluation: dict
    session: SessionOut
    replan_execution_id: str | None = None


# ---------------------------------------------------------------------- applications
class ApplicationIn(BaseModel):
    job_id: int | None = None
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    job_url: str | None = Field(default=None, max_length=1000)
    status: ApplicationStatus = ApplicationStatus.saved
    applied_on: date | None = None
    next_step: str = Field(default="", max_length=300)
    next_step_due: date | None = None
    notes: str = Field(default="", max_length=5000)

    @field_validator("job_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v and not re.match(r"^https?://", v):
            raise ValueError("job_url must start with http:// or https://")
        return v


class ApplicationPatch(BaseModel):
    status: ApplicationStatus | None = None
    applied_on: date | None = None
    next_step: str | None = Field(default=None, max_length=300)
    next_step_due: date | None = None
    notes: str | None = Field(default=None, max_length=5000)


class ApplicationOut(ORM):
    id: int
    job_id: int | None
    company: str
    title: str
    job_url: str | None
    status: str
    applied_on: date | None
    next_step: str
    next_step_due: date | None
    notes: str
    status_history: list[dict]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------- workflows
class CareerAnalysisIn(BaseModel):
    target_role: str | None = Field(default=None, max_length=120)
    deadline: date | None = None
    weekly_hours: float | None = Field(default=None, ge=0.5, le=80)
    reanalyze: bool = False


class WorkflowInputIn(BaseModel):
    target_role: str = Field(min_length=2, max_length=120)


class WorkflowOut(ORM):
    id: str
    kind: str
    status: str
    current_node: str | None
    steps: list[dict]
    pending_question: dict | None
    result: dict | None
    error: dict | None
    revision_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AssessmentOut(ORM):
    id: int
    source: str
    session_id: int | None
    skill: str | None
    competency: str
    score: float
    created_at: datetime


__all__ = ["EvidenceLevel"]
