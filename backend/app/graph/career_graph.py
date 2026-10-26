"""Career Orchestrator: the main LangGraph workflow.

    START -> validate_request --(invalid)--> finalize_failure -> END
                 | (valid)
                 v
            load_profile -> resume_analysis --(no evidence)--> finalize_failure
                 |
                 v
            determine_target  (interrupt() for clarification when the target role is unknown)
                 |
         +-------+--------+            parallel branches (same superstep)
         v                v
    job_research   resource_retrieval
         +-------+--------+            join: skill_gap waits for both
                 v
             skill_gap -> roadmap -> evaluate --(valid)--> persist -> END
                              ^           |
                              |       (invalid, revision < MAX)
                              +--- revise <+   (revise may route back to skill_gap for upstream issues)
                                          |
                                (invalid, budget exhausted) -> persist (status=partial, issues recorded)

Nodes call specialized agents; the orchestrator only routes, retries and persists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.agents.evaluation_agent import CareerEvaluationAgent
from app.agents.job_research_agent import JobResearchAgent
from app.agents.resume_agent import ResumeAnalysisAgent
from app.agents.roadmap_agent import PlanParams, RoadmapAgent
from app.agents.skill_gap_agent import SkillGapAgent
from app.core.config import get_settings
from app.db.session import session_scope
from app.graph.checkpoint import get_checkpointer
from app.graph.state import CareerState
from app.graph.tracking import tracked
from app.models import CandidateProfile, ExtractedSkill, JobListing, JobMatch, Resume
from app.models.enums import ResumeStatus
from app.repositories import career as repo
from app.schemas.agents import (
    EvaluationReport,
    JobResearchResult,
    ResumeAnalysis,
    RoadmapPlan,
    SkillGapReport,
)
from app.services.llm import get_llm
from app.services.roles import ROLES, resolve_role
from app.tools.job_providers import build_providers

DB_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, retry_on=OperationalError)
DEFAULT_WEEKS = 12
TOP_JOBS_FOR_GAPS = 10


def _err(node: str, message: str, *, fatal: bool = False) -> dict:
    return {"node": node, "message": message, "fatal": fatal}


# ---------------------------------------------------------------------- nodes
@tracked("validate_request")
def validate_request(state: CareerState) -> dict:
    req = state.get("request") or {}
    errors = []
    wh = req.get("weekly_hours")
    if wh is not None and not (0.5 <= float(wh) <= 80):
        errors.append(_err("validate_request", "weekly_hours must be between 0.5 and 80", fatal=True))
    deadline = req.get("deadline")
    if deadline and date.fromisoformat(deadline) <= date.today():
        errors.append(_err("validate_request", "deadline must be in the future", fatal=True))
    with session_scope() as db:
        if db.scalar(select(CandidateProfile.id).where(CandidateProfile.user_id == state["user_id"])) is None:
            errors.append(_err("validate_request", "Complete onboarding before running the analysis", fatal=True))
    return {"errors": errors, "revision": 0, "plan_params": {}, "status": "running"}


@tracked("load_profile")
def load_profile(state: CareerState) -> dict:
    req = state.get("request") or {}
    with session_scope() as db:
        profile = repo.get_or_create_profile(db, state["user_id"])
        pref = repo.get_or_create_preference(db, state["user_id"])
        start = date.today()
        deadline_s = req.get("deadline") or (pref.target_deadline.isoformat() if pref.target_deadline else None)
        warnings = []
        if deadline_s is None:
            # The deadline day is inclusive, so N weeks end on day 7N - 1.
            deadline = start + timedelta(weeks=DEFAULT_WEEKS, days=-1)
            warnings.append(f"No target deadline set; planning for {DEFAULT_WEEKS} weeks.")
        else:
            deadline = date.fromisoformat(deadline_s)
        constraints = {
            "weekly_hours": float(req.get("weekly_hours") or pref.weekly_study_hours or 8),
            "study_days": int(pref.study_days_per_week or 5),
            "start": start.isoformat(),
            "deadline": deadline.isoformat(),
            "weeks": repo.weeks_until(deadline, start),
            "locations": list(req.get("locations") or pref.preferred_locations or []),
        }
        return {"profile_id": profile.id, "constraints": constraints, "warnings": warnings}


@tracked("resume_analysis")
def resume_analysis(state: CareerState) -> dict:
    req = state.get("request") or {}
    with session_scope() as db:
        profile = db.get(CandidateProfile, state["profile_id"])
        resume = db.get(Resume, profile.active_resume_id) if profile.active_resume_id else None
        if (
            resume is not None
            and resume.status == ResumeStatus.analyzed
            and resume.analysis
            and not req.get("reanalyze")
        ):
            return {"resume_analysis": ResumeAnalysis.model_validate(resume.analysis).model_dump(mode="json")}
        if resume is not None and resume.raw_text:
            analysis = ResumeAnalysisAgent(get_llm()).analyze(resume.raw_text)
            repo.apply_resume_analysis(db, profile, resume, analysis)
            return {"resume_analysis": analysis.model_dump(mode="json"), "warnings": analysis.warnings}
        has_skills = db.scalar(select(ExtractedSkill.id).where(ExtractedSkill.profile_id == profile.id).limit(1))
        if has_skills:
            return {
                "resume_analysis": None,
                "warnings": ["No resume uploaded; using the skills saved on your profile."],
            }
    return {"errors": [_err("resume_analysis", "Upload a resume or add skills to your profile first", fatal=True)]}


@tracked("determine_target")
def determine_target(state: CareerState) -> dict:
    req = state.get("request") or {}
    with session_scope() as db:
        profile = db.get(CandidateProfile, state["profile_id"])
        target = req.get("target_role") or profile.target_role
        headline = profile.headline
    role = resolve_role(target) if target else None
    if not target:
        # Human-in-the-loop: pause until the user tells us which role to target (checkpointed).
        guess = resolve_role(headline)
        answer = interrupt(
            {
                "type": "clarification",
                "field": "target_role",
                "question": "Which role are you preparing for?",
                "options": [r.title for r in ROLES],
                "suggested": guess.title if guess else None,
            }
        )
        target = str((answer or {}).get("target_role") or "").strip()[:120]
        if not target:
            return {"errors": [_err("determine_target", "A target role is required", fatal=True)]}
        role = resolve_role(target)
        with session_scope() as db:
            db.get(CandidateProfile, state["profile_id"]).target_role = target
    warnings = [] if role else [f'"{target}" did not match a known role template; demand comes from job listings only.']
    return {"target_role": target, "role_key": role.key if role else None, "warnings": warnings}


@tracked("job_research")
def job_research(state: CareerState) -> dict:
    s = get_settings()
    providers = build_providers(
        s.job_providers,
        greenhouse_boards=s.greenhouse_boards,
        lever_companies=s.lever_companies,
        timeout=s.job_http_timeout_seconds,
        stale_after_days=s.job_stale_after_days,
    )
    agent = JobResearchAgent(providers, max_workers=s.workflow_workers)
    discovery = agent.discover(state["target_role"], state["constraints"].get("locations"))
    errors = [_err("job_research", f"{e['provider']}: {e['error']}") for e in discovery.errors]
    with session_scope() as db:
        stored = repo.upsert_jobs(db, discovery.jobs)
        if not stored:
            # Providers failed or returned nothing: fall back to cached listings (flagged by their timestamps).
            kws = agent.keywords_for(state["target_role"])
            cached = [j for j in db.scalars(select(JobListing).limit(500)) if any(k in j.title.lower() for k in kws)]
            stored = [(j.id, repo.job_to_normalized(j)) for j in cached]
        have = repo.candidate_skill_map(db, state["profile_id"])
        profile = db.get(CandidateProfile, state["profile_id"])
        prefs = repo.preferences_for(repo.get_or_create_preference(db, state["user_id"]))
        ranked = agent.rank(stored, have, profile.years_experience, prefs, s.job_stale_after_days, state["target_role"])
        repo.save_matches(db, state["profile_id"], ranked)
    result = JobResearchResult(
        providers_used=discovery.providers_used,
        provider_errors=discovery.errors,
        fetched=discovery.fetched,
        after_dedupe=len(discovery.jobs),
        job_ids=[m.job_id for m in ranked],
        matches=ranked[:25],
        demo_data=any(j.is_demo for _, j in stored),
    )
    warnings = ["Job listings are labeled DEMO data (mock provider)."] if result.demo_data else []
    return {"job_research": result.model_dump(mode="json"), "errors": errors, "warnings": warnings}


@tracked("resource_retrieval")
def resource_retrieval(state: CareerState) -> dict:
    role = next((r for r in ROLES if r.key == state.get("role_key")), None)
    skills = sorted(
        {
            *(role.required if role else ()),
            *(role.preferred if role else ()),
            "Data Structures & Algorithms",
            "System Design",
            "Behavioral Interviewing",
        }
    )
    with session_scope() as db:
        repo.seed_resources(db)
        found = repo.retrieve_resources(db, skills, context=state.get("target_role", ""))
    return {"resources": found}


@tracked("skill_gap")
def skill_gap(state: CareerState) -> dict:
    research = JobResearchResult.model_validate(state["job_research"]) if state.get("job_research") else None
    with session_scope() as db:
        have = repo.candidate_skill_map(db, state["profile_id"])
        job_reqs = []
        if research:
            ids = [m.job_id for m in research.matches[: TOP_JOBS_FOR_GAPS * 2]]
            rows = {j.id: j for j in db.scalars(select(JobListing).where(JobListing.id.in_(ids)))} if ids else {}
            for jid in ids:
                job = rows.get(jid)
                # Listings with instruction-like content never influence demand.
                if job and "suspicious_content" not in (job.quality_flags or []):
                    job_reqs.append(repo.job_to_normalized(job).requirements)
                if len(job_reqs) >= TOP_JOBS_FOR_GAPS:
                    break
    role = next((r for r in ROLES if r.key == state.get("role_key")), None)
    c = state["constraints"]
    report = SkillGapAgent().analyze(
        target_role=state["target_role"],
        role=role,
        job_requirements=job_reqs,
        have=have,
        weeks_until_deadline=c.get("weeks"),
        weekly_hours=c["weekly_hours"],
    )
    return {"gap_report": report.model_dump(mode="json")}


@tracked("roadmap")
def roadmap(state: CareerState) -> dict:
    report = SkillGapReport.model_validate(state["gap_report"])
    resources = dict(state.get("resources") or {})
    missing = [g.skill for g in report.items if g.skill not in resources]
    if missing:
        with session_scope() as db:
            repo.seed_resources(db)
            resources.update(repo.retrieve_resources(db, missing, context=state.get("target_role", "")))
    role = next((r for r in ROLES if r.key == state.get("role_key")), None)
    c = state["constraints"]
    pp = state.get("plan_params") or {}
    params = PlanParams(
        start=date.fromisoformat(c["start"]),
        end=date.fromisoformat(c["deadline"]),
        weekly_hours=c["weekly_hours"],
        study_days=c["study_days"],
        target_role=state["target_role"],
        tracks=role.interview_tracks if role else (),
        max_skill_share=pp.get("max_skill_share", 0.35),
        forced_skills=pp.get("forced_skills", []),
    )
    plan = RoadmapAgent().plan(report.items, params, resources)
    return {"plan": plan.model_dump(mode="json"), "resources": resources}


@tracked("evaluate")
def evaluate(state: CareerState) -> dict:
    with session_scope() as db:
        assessments = repo.latest_assessments(db, state["profile_id"])
        previous = repo.last_readiness(db, state["profile_id"])
    plan = RoadmapPlan.model_validate(state["plan"])
    known = {r["id"] for rs in (state.get("resources") or {}).values() for r in rs}
    report = CareerEvaluationAgent().evaluate(
        analysis=ResumeAnalysis.model_validate(state["resume_analysis"]) if state.get("resume_analysis") else None,
        gaps=SkillGapReport.model_validate(state["gap_report"]),
        plan=plan,
        known_resource_ids=known,
        assessments=assessments,
        previous_readiness=previous,
        progress=(0.0, plan.planned_hours),
    )
    return {"evaluation": report.model_dump(mode="json")}


@tracked("revise")
def revise(state: CareerState) -> dict:
    """Translate evaluation errors into concrete planning adjustments for the next attempt."""
    ev = EvaluationReport.model_validate(state["evaluation"])
    pp = dict(state.get("plan_params") or {})
    forced = list(pp.get("forced_skills", []))
    changes = []
    for issue in ev.issues:
        if issue.severity != "error":
            continue
        if issue.code == "top_gap_uncovered" and issue.skill and issue.skill not in forced:
            forced.append(issue.skill)
            changes.append(f"force {issue.skill}")
    if any(i.code == "top_gap_uncovered" for i in ev.issues):
        pp["max_skill_share"] = round(max(0.15, pp.get("max_skill_share", 0.35) - 0.1), 2)
        changes.append(f"max_skill_share={pp['max_skill_share']}")
    pp["forced_skills"] = forced
    n = state.get("revision", 0) + 1
    upstream = any(i.code in ("contradiction", "unsupported_skill") for i in ev.issues if i.severity == "error")
    return {
        "plan_params": pp,
        "revision": n,
        "revision_history": [
            {
                "revision": n,
                "issues": [i.code for i in ev.issues if i.severity == "error"],
                "changes": changes,
                "rerun_from": "skill_gap" if upstream else "roadmap",
            }
        ],
    }


@tracked("persist")
def persist(state: CareerState) -> dict:
    plan = RoadmapPlan.model_validate(state["plan"])
    ev = EvaluationReport.model_validate(state["evaluation"])
    report = SkillGapReport.model_validate(state["gap_report"])
    with session_scope() as db:
        snapshot = repo.save_gap_snapshot(db, state["profile_id"], report)
        rm = repo.save_new_roadmap(
            db,
            state["profile_id"],
            plan,
            execution_id=state.get("execution_id"),
            evaluation=ev.model_dump(mode="json"),
            revisions=state.get("revision", 0),
        )
        repo.record_readiness(db, state["profile_id"], ev.readiness.model_dump(mode="json"))
        saved_matches = db.scalar(select(JobMatch.id).where(JobMatch.profile_id == state["profile_id"]).limit(1))
        roadmap_id = rm.id
    errors = [e for e in state.get("errors", [])]
    status = "succeeded" if ev.valid and not errors else "partial"
    research = state.get("job_research") or {}
    return {
        "status": status,
        "result": {
            "roadmap_id": roadmap_id,
            "gap_snapshot_id": snapshot,
            "readiness": ev.readiness.score,
            "evaluation_valid": ev.valid,
            "revisions": state.get("revision", 0),
            "jobs_ranked": len(research.get("job_ids", [])),
            "has_matches": bool(saved_matches),
            "demo_data": research.get("demo_data", False),
            "explanation": ev.explanation,
            "issues": [i.model_dump() for i in ev.issues],
            "warnings": state.get("warnings", []),
            "completed_at": datetime.now(UTC).isoformat(),
        },
    }


def finalize_failure(state: CareerState) -> dict:
    fatal = [e for e in state.get("errors", []) if e.get("fatal")]
    return {"status": "failed", "result": {"errors": fatal or state.get("errors", [])}}


# ---------------------------------------------------------------------- routing
def _has_fatal(state: CareerState) -> bool:
    return any(e.get("fatal") for e in state.get("errors", []))


def route_after_validate(state: CareerState) -> str:
    return "finalize_failure" if _has_fatal(state) else "load_profile"


def route_after_resume(state: CareerState) -> str:
    return "finalize_failure" if _has_fatal(state) else "determine_target"


def route_after_target(state: CareerState) -> list[str] | str:
    if _has_fatal(state):
        return "finalize_failure"
    return ["job_research", "resource_retrieval"]  # fan out in parallel


def route_after_evaluate(state: CareerState) -> str:
    ev = state.get("evaluation") or {}
    if ev.get("valid"):
        return "persist"
    if state.get("revision", 0) < get_settings().max_revision_loops:
        return "revise"
    return "persist"  # bounded: give up revising, persist with issues recorded


def route_after_revise(state: CareerState) -> str:
    last = (state.get("revision_history") or [{}])[-1]
    return last.get("rerun_from", "roadmap")


def build_career_graph(checkpointer=None):
    g = StateGraph(CareerState)
    g.add_node("validate_request", validate_request)
    g.add_node("load_profile", load_profile, retry_policy=DB_RETRY)
    g.add_node("resume_analysis", resume_analysis, retry_policy=DB_RETRY)
    g.add_node("determine_target", determine_target)
    g.add_node("job_research", job_research, retry_policy=DB_RETRY)
    g.add_node("resource_retrieval", resource_retrieval, retry_policy=DB_RETRY)
    g.add_node("skill_gap", skill_gap, retry_policy=DB_RETRY)
    g.add_node("roadmap", roadmap)
    g.add_node("evaluate", evaluate, retry_policy=DB_RETRY)
    g.add_node("revise", revise)
    g.add_node("persist", persist, retry_policy=DB_RETRY)
    g.add_node("finalize_failure", finalize_failure)

    g.add_edge(START, "validate_request")
    g.add_conditional_edges("validate_request", route_after_validate, ["load_profile", "finalize_failure"])
    g.add_edge("load_profile", "resume_analysis")
    g.add_conditional_edges("resume_analysis", route_after_resume, ["determine_target", "finalize_failure"])
    g.add_conditional_edges(
        "determine_target", route_after_target, ["job_research", "resource_retrieval", "finalize_failure"]
    )
    g.add_edge(["job_research", "resource_retrieval"], "skill_gap")  # join
    g.add_edge("skill_gap", "roadmap")
    g.add_edge("roadmap", "evaluate")
    g.add_conditional_edges("evaluate", route_after_evaluate, ["persist", "revise"])
    g.add_conditional_edges("revise", route_after_revise, ["roadmap", "skill_gap"])
    g.add_edge("persist", END)
    g.add_edge("finalize_failure", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else get_checkpointer())
