"""Agent workflow execution: start, poll, answer clarification requests, inspect graphs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_profile, current_user, rate_limited
from app.core.errors import AppError, NotFoundError
from app.db.session import get_db
from app.graph.runner import runner
from app.models import AssessmentResult, CandidateProfile, User, WorkflowExecution
from app.models.enums import WorkflowKind
from app.schemas.api import AssessmentOut, CareerAnalysisIn, WorkflowInputIn, WorkflowOut

router = APIRouter(tags=["workflows"])


@router.post("/workflows/career-analysis", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
def start_career_analysis(
    body: CareerAnalysisIn,
    user: User = Depends(current_user),
    profile: CandidateProfile = Depends(current_profile),
    _rl: None = Depends(rate_limited("workflow", "rate_limit_expensive_per_minute")),
) -> dict:
    request = body.model_dump(mode="json", exclude_none=True)
    ex_id = runner.start(WorkflowKind.career_analysis, user.id, profile.id, request)
    return {"execution_id": ex_id}


def _owned(db: Session, user: User, execution_id: str) -> WorkflowExecution:
    ex = db.get(WorkflowExecution, execution_id)
    if ex is None or ex.user_id != user.id:
        raise NotFoundError("Workflow execution not found")
    return ex


@router.get("/workflows", response_model=list[WorkflowOut])
def list_workflows(
    limit: int = Query(default=10, ge=1, le=50), user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[WorkflowOut]:
    rows = db.scalars(
        select(WorkflowExecution)
        .where(WorkflowExecution.user_id == user.id)
        .order_by(WorkflowExecution.created_at.desc())
        .limit(limit)
    )
    return [WorkflowOut.model_validate(r) for r in rows]


@router.get("/workflows/graph/{kind}", response_model=dict)
def graph_diagram(kind: str) -> dict:
    """Mermaid diagram of a compiled graph (for documentation and debugging)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graph.career_graph import build_career_graph
    from app.graph.interview_graph import build_interview_graph
    from app.graph.progress_graph import build_progress_graph

    builders = {"career": build_career_graph, "progress": build_progress_graph, "interview": build_interview_graph}
    if kind not in builders:
        raise NotFoundError("Unknown graph")
    return {"kind": kind, "mermaid": builders[kind](InMemorySaver()).get_graph().draw_mermaid()}


@router.get("/workflows/{execution_id}", response_model=WorkflowOut)
def get_workflow(execution_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> WorkflowOut:
    return WorkflowOut.model_validate(_owned(db, user, execution_id))


@router.post("/workflows/{execution_id}/input", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
def provide_input(
    execution_id: str, body: WorkflowInputIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    ex = _owned(db, user, execution_id)
    if not ex.pending_question:
        raise AppError("This workflow is not waiting for input", code="not_awaiting_input")
    runner.resume(execution_id, user.id, {"target_role": body.target_role})
    return {"execution_id": execution_id}


@router.get("/assessments", response_model=list[AssessmentOut])
def assessment_history(
    skill: str | None = None,
    source: str | None = Query(default=None, pattern="^(interview|readiness|roadmap_task)$"),
    limit: int = Query(default=100, ge=1, le=500),
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> list[AssessmentOut]:
    stmt = select(AssessmentResult).where(AssessmentResult.profile_id == profile.id)
    if skill:
        stmt = stmt.where(AssessmentResult.skill == skill)
    if source:
        stmt = stmt.where(AssessmentResult.source == source)
    rows = db.scalars(stmt.order_by(AssessmentResult.created_at.desc()).limit(limit))
    return [AssessmentOut.model_validate(r) for r in rows]
