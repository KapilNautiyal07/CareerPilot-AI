"""Progress loop: user progress / interview results -> re-evaluate gaps -> update roadmap.

    START -> load_progress -> reevaluate_gaps -> update_roadmap -> evaluate_progress
                                                     ^                  |
                                                     +--- (invalid, revision < MAX) --+
                                                                        | (valid or budget spent)
                                                                        v
                                                                 persist_progress -> END

Triggered after an interview session completes, when the user asks to replan, or when tasks
are overdue. Reuses the stored job matches (no new job search) so it is cheap and fast.
"""

from __future__ import annotations

from datetime import date

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.agents.evaluation_agent import CareerEvaluationAgent
from app.agents.roadmap_agent import TRACK_SKILLS, PlanParams, RoadmapAgent, replan_params
from app.agents.skill_gap_agent import SkillGapAgent
from app.core.config import get_settings
from app.db.session import session_scope
from app.graph.career_graph import TOP_JOBS_FOR_GAPS
from app.graph.checkpoint import get_checkpointer
from app.graph.state import ProgressState
from app.graph.tracking import tracked
from app.models import CandidateProfile, JobListing, JobMatch
from app.models.enums import TaskStatus
from app.repositories import career as repo
from app.schemas.agents import EvaluationReport, RoadmapPlan, SkillGapReport
from app.services.roles import resolve_role

DB_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, retry_on=OperationalError)


@tracked("load_progress")
def load_progress(state: ProgressState) -> dict:
    today = date.today()
    with session_scope() as db:
        profile = db.get(CandidateProfile, state["profile_id"])
        rm = repo.active_roadmap(db, profile.id)
        if rm is None:
            return {
                "errors": [
                    {
                        "node": "load_progress",
                        "message": "No active roadmap; run the career analysis first",
                        "fatal": True,
                    }
                ]
            }
        live = [t for t in rm.tasks if t.status != TaskStatus.superseded]
        missed = [t for t in live if t.status == TaskStatus.pending and t.due_date < today]
        progress = {
            "roadmap_id": rm.id,
            "target_role": rm.target_role or profile.target_role,
            "start": rm.start_date.isoformat(),
            "end": rm.end_date.isoformat(),
            "weekly_hours": rm.weekly_hours,
            "study_days": rm.study_days_per_week,
            "tasks": [
                {
                    "id": t.id,
                    "skill": t.skill,
                    "status": t.status.value,
                    "estimated_hours": t.estimated_hours,
                    "due_date": t.due_date.isoformat(),
                }
                for t in live
            ],
            "missed": len(missed),
            "assessments": repo.latest_assessments(db, profile.id),
        }
    return {"progress": progress, "revision": 0, "plan_params": {}, "status": "running"}


@tracked("reevaluate_gaps")
def reevaluate_gaps(state: ProgressState) -> dict:
    p = state["progress"]
    with session_scope() as db:
        have = repo.candidate_skill_map(db, state["profile_id"])
        rows = (
            db.execute(
                select(JobListing)
                .join(JobMatch, JobMatch.job_id == JobListing.id)
                .where(JobMatch.profile_id == state["profile_id"])
                .order_by(JobMatch.score.desc())
                .limit(TOP_JOBS_FOR_GAPS * 2)
            )
            .scalars()
            .all()
        )
        reqs = [
            repo.job_to_normalized(j).requirements for j in rows if "suspicious_content" not in (j.quality_flags or [])
        ][:TOP_JOBS_FOR_GAPS]
    deadline = date.fromisoformat(p["end"])
    report = SkillGapAgent().analyze(
        target_role=p["target_role"],
        role=resolve_role(p["target_role"]),
        job_requirements=reqs,
        have=have,
        weeks_until_deadline=repo.weeks_until(deadline),
        weekly_hours=p["weekly_hours"],
    )
    return {"gap_report": report.model_dump(mode="json")}


@tracked("update_roadmap")
def update_roadmap(state: ProgressState) -> dict:
    p = state["progress"]
    report = SkillGapReport.model_validate(state["gap_report"])
    role = resolve_role(p["target_role"])
    base = PlanParams(
        start=date.fromisoformat(p["start"]),
        end=date.fromisoformat(p["end"]),
        weekly_hours=p["weekly_hours"],
        study_days=p["study_days"],
        target_role=p["target_role"],
        tracks=role.interview_tracks if role else (),
        max_skill_share=(state.get("plan_params") or {}).get("max_skill_share", 0.35),
    )
    params = replan_params(base, tasks=p["tasks"], assessments=p["assessments"], today=date.today())
    params.forced_skills = list(
        dict.fromkeys([*params.forced_skills, *(state.get("plan_params") or {}).get("forced_skills", [])])
    )
    with session_scope() as db:
        repo.seed_resources(db)
        # Interview-track skills feed fixed tasks (DSA maintenance, STAR prep) even when not open gaps.
        skills = list(dict.fromkeys([*(g.skill for g in report.items), *sorted(TRACK_SKILLS)]))
        resources = repo.retrieve_resources(db, skills, context=p["target_role"])
    plan = RoadmapAgent().plan(report.items, params, resources)
    changes = {
        "mastered": sorted(params.mastered),
        "remediation": sorted(params.remediation),
        "missed_tasks": p["missed"],
        "completed_hours": round(sum(params.completed_hours.values()), 1),
    }
    return {
        "plan": plan.model_dump(mode="json"),
        "progress": {**p, "changes": changes, "known_resources": [r["id"] for rs in resources.values() for r in rs]},
    }


@tracked("evaluate_progress")
def evaluate_progress(state: ProgressState) -> dict:
    p = state["progress"]
    plan = RoadmapPlan.model_validate(state["plan"])
    done = sum(t["estimated_hours"] for t in p["tasks"] if t["status"] == "completed")
    with session_scope() as db:
        previous = repo.last_readiness(db, state["profile_id"])
    report = CareerEvaluationAgent().evaluate(
        analysis=None,
        gaps=SkillGapReport.model_validate(state["gap_report"]),
        plan=plan,
        known_resource_ids=set(p.get("known_resources", [])),
        assessments=p["assessments"],
        previous_readiness=previous,
        progress=(round(done, 1), round(done + plan.planned_hours, 1)),
    )
    return {"evaluation": report.model_dump(mode="json")}


@tracked("revise_progress")
def revise_progress(state: ProgressState) -> dict:
    ev = EvaluationReport.model_validate(state["evaluation"])
    pp = dict(state.get("plan_params") or {})
    forced = list(pp.get("forced_skills", []))
    forced += [i.skill for i in ev.issues if i.code == "top_gap_uncovered" and i.skill and i.skill not in forced]
    pp["forced_skills"] = forced
    pp["max_skill_share"] = round(max(0.15, pp.get("max_skill_share", 0.35) - 0.1), 2)
    n = state.get("revision", 0) + 1
    return {
        "plan_params": pp,
        "revision": n,
        "revision_history": [{"revision": n, "issues": [i.code for i in ev.issues if i.severity == "error"]}],
    }


@tracked("persist_progress")
def persist_progress(state: ProgressState) -> dict:
    p = state["progress"]
    plan = RoadmapPlan.model_validate(state["plan"])
    ev = EvaluationReport.model_validate(state["evaluation"])
    with session_scope() as db:
        snapshot = repo.save_gap_snapshot(db, state["profile_id"], SkillGapReport.model_validate(state["gap_report"]))
        rm = repo.active_roadmap(db, state["profile_id"])
        repo.apply_replan(
            db,
            rm,
            plan,
            reason=state.get("trigger", "manual"),
            evaluation=ev.model_dump(mode="json"),
            changes=p.get("changes", {}),
        )
        repo.record_readiness(db, state["profile_id"], ev.readiness.model_dump(mode="json"))
        version = rm.version
    return {
        "status": "succeeded" if ev.valid else "partial",
        "result": {
            "roadmap_id": p["roadmap_id"],
            "roadmap_version": version,
            "gap_snapshot_id": snapshot,
            "readiness": ev.readiness.score,
            "readiness_delta": ev.readiness.delta,
            "changes": p.get("changes", {}),
            "explanation": ev.explanation,
            "issues": [i.model_dump() for i in ev.issues],
        },
    }


def finalize_progress_failure(state: ProgressState) -> dict:
    return {"status": "failed", "result": {"errors": state.get("errors", [])}}


def _after_load(state: ProgressState) -> str:
    return "finalize_progress_failure" if any(e.get("fatal") for e in state.get("errors", [])) else "reevaluate_gaps"


def _after_eval(state: ProgressState) -> str:
    if (state.get("evaluation") or {}).get("valid"):
        return "persist_progress"
    return "revise_progress" if state.get("revision", 0) < get_settings().max_revision_loops else "persist_progress"


def build_progress_graph(checkpointer=None):
    g = StateGraph(ProgressState)
    g.add_node("load_progress", load_progress, retry_policy=DB_RETRY)
    g.add_node("reevaluate_gaps", reevaluate_gaps, retry_policy=DB_RETRY)
    g.add_node("update_roadmap", update_roadmap, retry_policy=DB_RETRY)
    g.add_node("evaluate_progress", evaluate_progress, retry_policy=DB_RETRY)
    g.add_node("revise_progress", revise_progress)
    g.add_node("persist_progress", persist_progress, retry_policy=DB_RETRY)
    g.add_node("finalize_progress_failure", finalize_progress_failure)
    g.add_edge(START, "load_progress")
    g.add_conditional_edges("load_progress", _after_load, ["reevaluate_gaps", "finalize_progress_failure"])
    g.add_edge("reevaluate_gaps", "update_roadmap")
    g.add_edge("update_roadmap", "evaluate_progress")
    g.add_conditional_edges("evaluate_progress", _after_eval, ["persist_progress", "revise_progress"])
    g.add_edge("revise_progress", "update_roadmap")
    g.add_edge("persist_progress", END)
    g.add_edge("finalize_progress_failure", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else get_checkpointer())
