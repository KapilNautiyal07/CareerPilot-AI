"""String enums shared by models, schemas and agents."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def db_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """VARCHAR + CHECK constraint (portable, and easy to extend with a migration)."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class WorkArrangement(StrEnum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    unknown = "unknown"


class Seniority(StrEnum):
    intern = "intern"
    junior = "junior"
    mid = "mid"
    senior = "senior"
    staff = "staff"
    lead = "lead"
    unknown = "unknown"


class ResumeStatus(StrEnum):
    uploaded = "uploaded"
    parsed = "parsed"
    analyzed = "analyzed"
    failed = "failed"


class SkillSource(StrEnum):
    explicit = "explicit"  # stated in the resume
    inferred = "inferred"  # implied by another stated skill
    user = "user"  # added/confirmed by the user


class EvidenceLevel(StrEnum):
    assessed = "assessed"  # measured by an assessment in this app
    demonstrated = "demonstrated"  # used in described work/project
    listed = "listed"  # only appears in a skills list
    inferred = "inferred"
    user_asserted = "user_asserted"


class RequirementLevel(StrEnum):
    required = "required"
    preferred = "preferred"


class GapStatus(StrEnum):
    matched = "matched"
    partial = "partial"
    missing = "missing"


class RoadmapStatus(StrEnum):
    active = "active"
    superseded = "superseded"
    archived = "archived"


class TaskType(StrEnum):
    learn = "learn"
    practice = "practice"
    project = "project"
    revision = "revision"
    assessment = "assessment"
    mock_interview = "mock_interview"


class TaskStatus(StrEnum):
    pending = "pending"
    completed = "completed"
    skipped = "skipped"
    superseded = "superseded"


class ResourceVerification(StrEnum):
    unverified = "unverified"
    reachable = "reachable"
    unreachable = "unreachable"


class InterviewCategory(StrEnum):
    dsa = "dsa"
    python = "python"
    backend = "backend"
    system_design = "system_design"
    behavioral = "behavioral"
    project = "project"
    mixed = "mixed"


class InterviewStatus(StrEnum):
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"


class QuestionKind(StrEnum):
    open = "open"
    code = "code"
    behavioral = "behavioral"


class AssessmentSource(StrEnum):
    interview = "interview"
    roadmap_task = "roadmap_task"
    readiness = "readiness"


class ApplicationStatus(StrEnum):
    saved = "saved"
    planned = "planned"
    applied = "applied"
    interviewing = "interviewing"
    offered = "offered"
    rejected = "rejected"
    withdrawn = "withdrawn"


class WorkflowKind(StrEnum):
    career_analysis = "career_analysis"
    replan = "replan"


class WorkflowStatus(StrEnum):
    queued = "queued"
    running = "running"
    awaiting_input = "awaiting_input"
    succeeded = "succeeded"
    partial = "partial"  # finished with non-fatal step failures
    failed = "failed"
