"""Validated agent outputs. Graph nodes exchange these (as JSON dicts in checkpointed state)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.enums import (
    EvidenceLevel,
    GapStatus,
    RequirementLevel,
    Seniority,
    SkillSource,
    TaskType,
    WorkArrangement,
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- Resume analysis
class SkillEvidence(Strict):
    skill: str
    category: str = "other"
    source: SkillSource
    evidence_level: EvidenceLevel
    evidence: list[str] = Field(default_factory=list, description="Verbatim snippets from the resume")
    years: float | None = Field(default=None, ge=0, le=60)
    confidence: float = Field(ge=0, le=1)
    inferred_from: str | None = None


class WorkExperienceItem(Strict):
    title: str = ""
    company: str = ""
    location: str = ""
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    highlights: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class ProjectItem(Strict):
    name: str
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None
    highlights: list[str] = Field(default_factory=list)


class EducationItem(Strict):
    institution: str = ""
    degree: str = ""
    field: str = ""
    graduation_year: int | None = None
    raw: str = ""


class CertificationItem(Strict):
    name: str
    issuer: str = ""
    year: int | None = None


class ClaimIssue(Strict):
    claim: str
    reason: str


class ResumeAnalysis(Strict):
    candidate_name: str | None = None
    location: str | None = None
    headline: str | None = None
    summary: str = ""
    skills: list[SkillEvidence] = Field(default_factory=list)
    experiences: list[WorkExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    total_years_experience: float | None = Field(default=None, ge=0, le=60)
    missing_information: list[str] = Field(default_factory=list)
    unclear_claims: list[ClaimIssue] = Field(default_factory=list)
    security_flags: list[dict] = Field(default_factory=list)
    extraction_method: Literal["heuristic", "llm_grounded"] = "heuristic"
    warnings: list[str] = Field(default_factory=list)


class ResumeSuggestion(Strict):
    kind: Literal["add_evidence", "quantify", "clarify", "keyword_gap", "structure", "remove_unsupported"]
    section: str
    suggestion: str
    rationale: str
    skill: str | None = None


class ResumeTailoring(Strict):
    job_title: str
    matched_keywords: list[str]
    missing_required: list[str]
    missing_preferred: list[str]
    suggestions: list[ResumeSuggestion]
    disclaimer: str = (
        "Only add skills or experience you actually have. Missing items are gaps to close, not keywords to insert."
    )


# ---------------------------------------------------------------- Jobs
class SkillRequirement(Strict):
    skill: str
    level: RequirementLevel
    evidence_text: str = ""


class NormalizedJob(Strict):
    source: str
    external_id: str
    title: str
    company: str
    location: str = ""
    work_arrangement: WorkArrangement = WorkArrangement.unknown
    employment_type: str = "unknown"
    seniority: Seniority = Seniority.unknown
    min_years_experience: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    description: str = ""
    application_url: str | None = None
    source_url: str | None = None
    posted_at: datetime | None = None
    retrieved_at: datetime
    requirements: list[SkillRequirement] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    is_demo: bool = False


class SkillMatchDetail(Strict):
    skill: str
    level: RequirementLevel
    status: GapStatus
    credit: float = Field(ge=0, le=1)
    evidence_level: EvidenceLevel | None = None
    via: str | None = None  # related skill providing partial credit
    explanation: str


class JobMatchScore(Strict):
    job_id: int
    score: float = Field(ge=0, le=100)
    skill_score: float = Field(ge=0, le=100)
    experience_score: float = Field(ge=0, le=100)
    preference_score: float = Field(ge=0, le=100)
    recency_score: float = Field(ge=0, le=100)
    role_score: float = Field(default=60.0, ge=0, le=100)
    details: list[SkillMatchDetail]
    notes: list[str] = Field(default_factory=list)


class JobResearchResult(Strict):
    providers_used: list[str]
    provider_errors: list[dict] = Field(default_factory=list)
    fetched: int
    after_dedupe: int
    job_ids: list[int]
    matches: list[JobMatchScore]
    demo_data: bool


# ---------------------------------------------------------------- Skill gaps
class PriorityComponents(Strict):
    importance: float
    severity: float
    urgency: float
    effort_penalty: float
    formula: str = "priority = 100 * importance * severity * urgency * effort_penalty"


class SkillGapItem(Strict):
    skill: str
    category: str
    status: GapStatus
    requirement_level: RequirementLevel
    demand_share: float = Field(ge=0, le=1)
    evidence_strength: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    priority_score: float = Field(ge=0, le=100)
    components: PriorityComponents
    estimated_hours: float = Field(ge=0)
    transferable_from: list[str] = Field(default_factory=list)
    recommendation: str


class SkillGapReport(Strict):
    target_role: str
    demand_source: Literal["jobs", "role_template", "jobs+role_template"]
    jobs_considered: int
    weeks_until_deadline: float | None
    items: list[SkillGapItem]
    recommended_next: list[str]
    coverage: float = Field(ge=0, le=1, description="Weighted share of demanded skills already matched")


# ---------------------------------------------------------------- Roadmap
class PlannedTask(Strict):
    title: str
    description: str
    task_type: TaskType
    skill: str | None
    hours: float = Field(gt=0, le=40)
    scheduled_date: date
    due_date: date
    resource_ids: list[int] = Field(default_factory=list)
    origin: Literal["generated", "replanned", "remediation"] = "generated"


class WeekPlan(Strict):
    index: int
    start: date
    end: date
    focus: list[str]
    milestone: str
    planned_hours: float
    tasks: list[PlannedTask]


class RoadmapPlan(Strict):
    title: str
    target_role: str
    start_date: date
    end_date: date
    weekly_hours: float
    study_days_per_week: int
    capacity_hours: float
    planned_hours: float
    weeks: list[WeekPlan]
    covered_skills: list[str]
    uncovered_gaps: list[str]
    assumptions: list[str]


# ---------------------------------------------------------------- Evaluation
class EvaluationIssue(Strict):
    code: str
    severity: Literal["error", "warning"]
    message: str
    skill: str | None = None


class ReadinessComponent(Strict):
    name: str
    weight: float
    value: float = Field(ge=0, le=100)
    explanation: str


class ReadinessReport(Strict):
    score: float = Field(ge=0, le=100)
    components: list[ReadinessComponent]
    previous_score: float | None = None
    delta: float | None = None
    disclaimer: str = (
        "Readiness is a weighted summary of skill coverage, evidence, assessment results and plan progress "
        "inside CareerPilot. It is not a probability of receiving a job offer."
    )


class EvaluationReport(Strict):
    valid: bool
    issues: list[EvaluationIssue]
    checks: list[dict]
    readiness: ReadinessReport
    explanation: str


# ---------------------------------------------------------------- Interview
class CriterionResult(Strict):
    id: str
    description: str
    weight: float
    met: float = Field(ge=0, le=1)
    evidence: str | None = None


class CodeExecutionResult(Strict):
    status: Literal["passed", "failed", "error", "timeout", "not_executed"]
    passed: int = 0
    total: int = 0
    detail: str = ""
    static_issues: list[str] = Field(default_factory=list)


class AnswerEvaluation(Strict):
    score: float = Field(ge=0, le=100)
    criteria: list[CriterionResult]
    feedback: str
    mistakes: list[str]
    missed_concepts: list[str]
    follow_up: str | None = None
    follow_up_criterion: str | None = None
    code_result: CodeExecutionResult | None = None
    evaluator: str
    disclaimer: str = "Practice feedback generated by CareerPilot's rubric; not an employer's assessment."


class LLMRubricJudgement(Strict):
    """Schema the LLM must return when grading an answer (validated + grounded before use)."""

    class Item(Strict):
        id: str
        met: float = Field(ge=0, le=1)
        quote: str | None = Field(default=None, description="Verbatim quote from the answer supporting the score")

    criteria: list[Item]
    feedback: str


class LLMResumeExtraction(Strict):
    """Schema the LLM returns for resume extraction (grounded before merge)."""

    class Skill(Strict):
        name: str
        quote: str = Field(description="Verbatim text from the resume that mentions or demonstrates the skill")

    class Experience(Strict):
        title: str
        company: str
        start: str | None = None
        end: str | None = None
        quote: str

    skills: list[Skill]
    experiences: list[Experience]
    achievements: list[str]
    summary: str


class ResourceRef(Strict):
    id: int
    title: str
    url: HttpUrl | str
    skills: list[str]
    verification_status: str
