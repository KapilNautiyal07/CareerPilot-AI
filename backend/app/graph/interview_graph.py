"""Interactive mock interview as a checkpointed LangGraph (one thread per session).

    START -> prepare -> ask -> await_answer (interrupt) -> evaluate_answer --(more)--> ask
                                                               | (queue empty / ended / limit)
                                                               v
                                                           summarize -> END

`ask` persists the next question; `await_answer` only calls interrupt(), so resuming never
duplicates side effects. A low score on a criterion queues one follow-up question (bounded).
The workflow is independent of the career analysis and can be started from the dashboard.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.agents.interview_agent import InterviewCoachAgent, bank_item
from app.db.session import session_scope
from app.graph.checkpoint import get_checkpointer
from app.graph.state import InterviewState
from app.models import AssessmentResult, InterviewQuestion, InterviewResponse, InterviewSession, SkillGap
from app.models.enums import AssessmentSource, GapStatus, InterviewStatus, QuestionKind
from app.services.llm import get_llm

MAX_FOLLOW_UPS = 2


def prepare(state: InterviewState) -> dict:
    with session_scope() as db:
        session = db.get(InterviewSession, state["session_id"])
        weak = list(
            db.scalars(
                select(SkillGap.skill).where(
                    SkillGap.profile_id == session.profile_id,
                    SkillGap.is_current.is_(True),
                    SkillGap.status != GapStatus.matched,
                )
            )
        )
        recent = set(
            db.scalars(
                select(InterviewQuestion.bank_id)
                .join(InterviewSession)
                .where(InterviewSession.profile_id == session.profile_id)
                .order_by(InterviewQuestion.id.desc())
                .limit(30)
            )
        )
        seed = int(hashlib.sha256(session.thread_id.encode()).hexdigest()[:8], 16)
        picked = InterviewCoachAgent(get_llm()).select_questions(
            category=session.category.value,
            target_role=session.target_role,
            count=session.max_questions,
            weak_skills=weak,
            recent_bank_ids=recent,
            seed=seed,
            difficulty=session.difficulty,
        )
        max_q = session.max_questions
    return {
        "queue": [{"bank_id": q["id"], "is_follow_up": False} for q in picked],
        "asked": 0,
        "max_total": max_q + MAX_FOLLOW_UPS,
        "results": [],
        "ended_early": False,
    }


def ask(state: InterviewState) -> dict:
    queue = list(state["queue"])
    item = queue.pop(0)
    bank = bank_item(item["bank_id"])
    with session_scope() as db:
        if item["is_follow_up"]:
            parent = db.get(InterviewQuestion, item["parent_question_id"])
            crit = [r for r in parent.rubric if r["id"] == item["criterion_id"]]
            q = InterviewQuestion(
                session_id=state["session_id"],
                parent_question_id=parent.id,
                bank_id=parent.bank_id,
                order_index=state["asked"],
                kind=QuestionKind.open,
                category=parent.category,
                prompt=item["prompt"],
                skills=parent.skills,
                rubric=[{**c, "weight": 1.0} for c in crit],
                hints=[c.get("hint") for c in crit if c.get("hint")],
            )
        else:
            q = InterviewQuestion(
                session_id=state["session_id"],
                bank_id=bank["id"],
                order_index=state["asked"],
                kind=QuestionKind(bank["kind"]),
                category=bank["category"],
                prompt=bank["prompt"],
                skills=bank["skills"],
                rubric=bank["rubric"],
                hints=bank.get("hints", []),
                code_spec=bank.get("code_spec"),
            )
        db.add(q)
        db.flush()
        qid = q.id
    return {"queue": queue, "asked": state["asked"] + 1, "current_question_id": qid, "pending": None}


def await_answer(state: InterviewState) -> dict:
    answer = interrupt({"type": "answer", "question_id": state["current_question_id"]})
    return {"pending": answer}


def evaluate_answer(state: InterviewState) -> dict:
    payload = state.get("pending") or {}
    if payload.get("action") == "end":
        return {"ended_early": True, "pending": None}
    agent = InterviewCoachAgent(get_llm())
    with session_scope() as db:
        q = db.get(InterviewQuestion, state["current_question_id"])
        question = {
            "prompt": q.prompt,
            "rubric": q.rubric,
            "code_spec": q.code_spec,
            "is_follow_up": q.parent_question_id is not None,
        }
        ev = agent.evaluate(question, payload.get("answer", ""), payload.get("code"), hints_used=q.hints_revealed)
        db.add(
            InterviewResponse(
                question_id=q.id,
                answer_text=payload.get("answer", "")[:20_000],
                code=(payload.get("code") or None),
                score=ev.score,
                criteria_results=[c.model_dump() for c in ev.criteria],
                feedback=ev.feedback,
                mistakes=ev.mistakes,
                hints_used=q.hints_revealed,
                evaluator=ev.evaluator,
                code_execution=ev.code_result.model_dump() if ev.code_result else None,
            )
        )
        result = {
            "question_id": q.id,
            "skills": q.skills,
            "category": q.category,
            "score": ev.score,
            "criteria": [c.model_dump() for c in ev.criteria],
            "is_follow_up": q.parent_question_id is not None,
        }
        queue = list(state["queue"])
        follow_ups = sum(1 for r in state["results"] if r["is_follow_up"])
        if (
            ev.follow_up
            and not question["is_follow_up"]
            and follow_ups < MAX_FOLLOW_UPS
            and state["asked"] + len(queue) < state["max_total"]
        ):
            queue.insert(
                0,
                {
                    "bank_id": q.bank_id,
                    "is_follow_up": True,
                    "parent_question_id": q.id,
                    "criterion_id": ev.follow_up_criterion,
                    "prompt": ev.follow_up,
                },
            )
    return {"results": [*state["results"], result], "queue": queue, "pending": None}


def summarize(state: InterviewState) -> dict:
    summary = InterviewCoachAgent.summarize(state["results"])
    with session_scope() as db:
        session = db.get(InterviewSession, state["session_id"])
        session.status = InterviewStatus.completed if state["results"] else InterviewStatus.abandoned
        session.overall_score = summary["overall"] if state["results"] else None
        session.summary = {
            **summary,
            "ended_early": state.get("ended_early", False),
            "disclaimer": "Practice assessment generated by CareerPilot; not an employer's evaluation.",
        }
        session.completed_at = datetime.now(UTC)
        for skill, score in summary["competencies"].items():
            db.add(
                AssessmentResult(
                    profile_id=session.profile_id,
                    source=AssessmentSource.interview,
                    session_id=session.id,
                    skill=skill,
                    competency=session.category.value,
                    score=score,
                    details={"questions": sum(skill in r["skills"] for r in state["results"])},
                )
            )
    return {"summary": summary, "current_question_id": None}


def _after_eval(state: InterviewState) -> str:
    if state.get("ended_early") or not state["queue"] or state["asked"] >= state["max_total"]:
        return "summarize"
    return "ask"


def _after_prepare(state: InterviewState) -> str:
    return "ask" if state["queue"] else "summarize"


def build_interview_graph(checkpointer=None):
    g = StateGraph(InterviewState)
    g.add_node("prepare", prepare)
    g.add_node("ask", ask)
    g.add_node("await_answer", await_answer)
    g.add_node("evaluate_answer", evaluate_answer)
    g.add_node("summarize", summarize)
    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", _after_prepare, ["ask", "summarize"])
    g.add_edge("ask", "await_answer")
    g.add_edge("await_answer", "evaluate_answer")
    g.add_conditional_edges("evaluate_answer", _after_eval, ["ask", "summarize"])
    g.add_edge("summarize", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else get_checkpointer())
