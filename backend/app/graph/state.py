"""Typed shared state for the LangGraph workflows.

State holds JSON-serializable agent outputs (validated Pydantic models dumped with mode="json")
so it checkpoints cleanly. It never contains credentials, tokens or raw resume text; nodes
reload sensitive data from the database by id when needed.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CareerState(TypedDict, total=False):
    execution_id: str
    user_id: int
    profile_id: int
    request: dict[str, Any]
    target_role: str
    role_key: str | None
    constraints: dict[str, Any]  # weekly_hours, study_days, start, deadline, weeks
    resume_analysis: dict | None
    job_research: dict | None
    resources: dict[str, list[dict]]
    gap_report: dict | None
    plan: dict | None
    plan_params: dict[str, Any]  # max_skill_share, forced_skills (tuned by revisions)
    evaluation: dict | None
    revision: int
    revision_history: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]  # parallel branches may both append
    warnings: Annotated[list[str], operator.add]
    status: str
    result: dict | None


class ProgressState(TypedDict, total=False):
    execution_id: str
    user_id: int
    profile_id: int
    trigger: str  # interview_completed | task_update | manual
    progress: dict
    gap_report: dict | None
    plan: dict | None
    plan_params: dict[str, Any]
    evaluation: dict | None
    revision: int
    revision_history: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]
    warnings: Annotated[list[str], operator.add]
    status: str
    result: dict | None


class InterviewState(TypedDict, total=False):
    session_id: int
    profile_id: int
    queue: list[dict]
    asked: int
    max_total: int
    current_question_id: int | None
    pending: dict | None  # answer payload delivered by resume
    results: list[dict]
    ended_early: bool
    summary: dict | None
