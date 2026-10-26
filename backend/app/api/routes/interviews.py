"""Mock interviews. Each session is a checkpointed LangGraph thread that pauses at every question."""

from __future__ import annotations

import threading
import uuid

from fastapi import APIRouter, Depends, Query, status
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_profile, current_user, rate_limited
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger, log_ctx
from app.db.session import get_db
from app.graph.runner import runner
from app.models import CandidateProfile, InterviewQuestion, InterviewSession, User
from app.models.enums import InterviewStatus, WorkflowKind
from app.repositories import career as repo
from app.schemas.api import AnswerIn, AnswerOut, InterviewStartIn, QuestionOut, SessionOut

router = APIRouter(prefix="/interviews", tags=["interviews"])
logger = get_logger(__name__)
_graph = None
_graph_lock = threading.Lock()
_session_locks: dict[int, threading.Lock] = {}


def interview_graph():
    global _graph
    with _graph_lock:
        if _graph is None:
            from app.graph.interview_graph import build_interview_graph

            _graph = build_interview_graph()
        return _graph


def reset_interview_graph() -> None:
    global _graph
    with _graph_lock:
        _graph = None


def _lock_for(session_id: int) -> threading.Lock:
    with _graph_lock:
        return _session_locks.setdefault(session_id, threading.Lock())


def _question_out(q: InterviewQuestion) -> QuestionOut:
    resp = None
    if q.response is not None:
        r = q.response
        resp = {
            "answer_text": r.answer_text,
            "code": r.code,
            "score": r.score,
            "criteria": r.criteria_results,
            "feedback": r.feedback,
            "mistakes": r.mistakes,
            "hints_used": r.hints_used,
            "evaluator": r.evaluator,
            "code_execution": r.code_execution,
            "submitted_at": r.submitted_at,
        }
    return QuestionOut(
        id=q.id,
        order_index=q.order_index,
        kind=q.kind.value,
        category=q.category,
        prompt=q.prompt,
        skills=q.skills,
        is_follow_up=q.parent_question_id is not None,
        hints_available=len(q.hints),
        hints_revealed=q.hints[: q.hints_revealed],
        code_function=(q.code_spec or {}).get("function"),
        response=resp,
    )


def session_out(db: Session, session_id: int) -> SessionOut:
    s = db.scalar(
        select(InterviewSession)
        .options(selectinload(InterviewSession.questions).selectinload(InterviewQuestion.response))
        .where(InterviewSession.id == session_id)
        .execution_options(populate_existing=True)
    )
    questions = [_question_out(q) for q in s.questions]
    current = (
        next((q for q in questions if q.response is None), None) if s.status == InterviewStatus.in_progress else None
    )
    return SessionOut(
        id=s.id,
        category=s.category.value,
        target_role=s.target_role,
        difficulty=s.difficulty,
        status=s.status.value,
        max_questions=s.max_questions,
        overall_score=s.overall_score,
        summary=s.summary or {},
        started_at=s.started_at,
        completed_at=s.completed_at,
        questions=questions,
        current_question=current,
    )


def _owned(db: Session, profile: CandidateProfile, session_id: int) -> InterviewSession:
    s = db.get(InterviewSession, session_id)
    if s is None or s.profile_id != profile.id:
        raise NotFoundError("Interview session not found")
    return s


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def start_interview(
    body: InterviewStartIn,
    profile: CandidateProfile = Depends(current_profile),
    _rl: None = Depends(rate_limited("interview", "rate_limit_expensive_per_minute")),
    db: Session = Depends(get_db),
) -> SessionOut:
    role = (body.target_role or profile.target_role or "Software Engineer").strip()
    s = InterviewSession(
        profile_id=profile.id,
        category=body.category,
        target_role=role,
        difficulty=body.difficulty,
        max_questions=body.num_questions,
        thread_id=f"interview-{uuid.uuid4().hex}",
        summary={},
    )
    db.add(s)
    db.commit()
    interview_graph().invoke(
        {"session_id": s.id, "profile_id": profile.id}, {"configurable": {"thread_id": s.thread_id}}
    )
    return session_out(db, s.id)


@router.get("", response_model=list[SessionOut])
def list_interviews(
    limit: int = Query(default=20, ge=1, le=100),
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    ids = db.scalars(
        select(InterviewSession.id)
        .where(InterviewSession.profile_id == profile.id)
        .order_by(InterviewSession.started_at.desc())
        .limit(limit)
    )
    return [session_out(db, i) for i in ids]


@router.get("/{session_id}", response_model=SessionOut)
def get_interview(
    session_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> SessionOut:
    return session_out(db, _owned(db, profile, session_id).id)


def _resume(db: Session, s: InterviewSession, payload: dict) -> None:
    config = {"configurable": {"thread_id": s.thread_id}}
    graph = interview_graph()
    snapshot = graph.get_state(config)
    if not snapshot.interrupts:
        raise ConflictError("This interview is not waiting for an answer")
    graph.invoke(Command(resume=payload), config)


def _maybe_replan(user: User, profile: CandidateProfile, db: Session) -> str | None:
    """Interview results feed back into gaps and the roadmap ("re-evaluate gaps -> update roadmap")."""
    if repo.active_roadmap(db, profile.id) is None:
        return None
    try:
        return runner.start(WorkflowKind.replan, user.id, profile.id, {"trigger": "interview_completed"})
    except ConflictError:
        return None


@router.post("/{session_id}/answer", response_model=AnswerOut)
def submit_answer(
    session_id: int,
    body: AnswerIn,
    user: User = Depends(current_user),
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> AnswerOut:
    s = _owned(db, profile, session_id)
    if s.status != InterviewStatus.in_progress:
        raise ConflictError("This interview has already finished")
    if not body.answer.strip() and not (body.code or "").strip():
        raise AppError("Provide an answer or code", code="validation_error")
    with _lock_for(s.id):  # one answer at a time per session
        current = session_out(db, s.id).current_question
        if current is None:
            raise ConflictError("No question is awaiting an answer")
        _resume(db, s, {"answer": body.answer, "code": body.code})
    out = session_out(db, s.id)
    answered = next(q for q in out.questions if q.id == current.id)
    replan_id = _maybe_replan(user, profile, db) if out.status == InterviewStatus.completed.value else None
    logger.info("Interview answer evaluated", extra=log_ctx(session_id=s.id, question_id=current.id))
    return AnswerOut(evaluation=answered.response or {}, session=out, replan_execution_id=replan_id)


@router.post("/{session_id}/hint", response_model=QuestionOut)
def reveal_hint(
    session_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> QuestionOut:
    s = _owned(db, profile, session_id)
    current = session_out(db, s.id).current_question
    if current is None:
        raise ConflictError("No active question")
    q = db.get(InterviewQuestion, current.id)
    if q.hints_revealed >= len(q.hints):
        raise AppError("No more hints for this question", code="no_more_hints")
    q.hints_revealed += 1  # each hint used reduces this question's score by a fixed penalty
    db.commit()
    return _question_out(q)


@router.post("/{session_id}/end", response_model=AnswerOut)
def end_interview(
    session_id: int,
    user: User = Depends(current_user),
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> AnswerOut:
    s = _owned(db, profile, session_id)
    if s.status != InterviewStatus.in_progress:
        raise ConflictError("This interview has already finished")
    with _lock_for(s.id):
        _resume(db, s, {"action": "end"})
    out = session_out(db, s.id)
    replan_id = _maybe_replan(user, profile, db) if out.status == InterviewStatus.completed.value else None
    return AnswerOut(evaluation={}, session=out, replan_execution_id=replan_id)
