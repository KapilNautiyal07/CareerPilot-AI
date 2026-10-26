"""Analytics dashboard. Every number here is computed from persisted rows at request time."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_profile
from app.api.routes.learning import gap_history
from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    AssessmentResult,
    CandidateProfile,
    InterviewSession,
    JobApplication,
    JobListing,
    JobMatch,
    SkillGap,
    WorkflowExecution,
)
from app.models.enums import ApplicationStatus, AssessmentSource, GapStatus, InterviewStatus, TaskStatus
from app.repositories import career as repo

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=dict)
def dashboard(profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)) -> dict:
    today = date.today()
    pid = profile.id
    jobs_discovered = db.scalar(select(func.count()).select_from(JobMatch).where(JobMatch.profile_id == pid)) or 0
    relevant = (
        db.scalar(select(func.count()).select_from(JobMatch).where(JobMatch.profile_id == pid, JobMatch.score >= 60))
        or 0
    )
    demo_jobs = (
        db.scalar(
            select(func.count())
            .select_from(JobMatch)
            .join(JobListing)
            .where(JobMatch.profile_id == pid, JobListing.is_demo.is_(True))
        )
        or 0
    )
    saved = (
        db.scalar(
            select(func.count()).select_from(JobMatch).where(JobMatch.profile_id == pid, JobMatch.is_saved.is_(True))
        )
        or 0
    )
    by_status = dict(
        db.execute(
            select(JobApplication.status, func.count())
            .where(JobApplication.profile_id == pid)
            .group_by(JobApplication.status)
        ).all()
    )
    applications = {s.value: by_status.get(s, 0) for s in ApplicationStatus}

    gaps = list(
        db.scalars(
            select(SkillGap)
            .where(SkillGap.profile_id == pid, SkillGap.is_current.is_(True))
            .order_by(SkillGap.priority_score.desc())
        )
    )
    gap_counts = {s.value: sum(g.status == s for g in gaps) for s in GapStatus}
    history = gap_history(profile, db)
    improvements = _improvements(db, pid)

    rm = repo.active_roadmap(db, pid)
    roadmap = None
    upcoming: list[dict] = []
    overdue_tasks = 0
    if rm is not None:
        live = [t for t in rm.tasks if t.status != TaskStatus.superseded]
        done_h, total_h = repo.roadmap_progress(rm) or (0, 0)
        overdue_tasks = sum(t.status == TaskStatus.pending and t.due_date < today for t in live)
        roadmap = {
            "id": rm.id,
            "version": rm.version,
            "completed_hours": done_h,
            "total_hours": total_h,
            "percent": round(100 * done_h / total_h, 1) if total_h else 0.0,
            "completed_tasks": sum(t.status == TaskStatus.completed for t in live),
            "total_tasks": len(live),
            "end_date": rm.end_date,
        }
        upcoming = [
            {"week": m["week"], "start": m["start"], "end": m["end"], "milestone": m["milestone"]}
            for m in rm.summary.get("milestones", [])
            if date.fromisoformat(m["end"]) >= today
        ][:3]

    sessions = list(
        db.scalars(
            select(InterviewSession)
            .where(InterviewSession.profile_id == pid, InterviewSession.status == InterviewStatus.completed)
            .order_by(InterviewSession.completed_at)
        )
    )
    interview_trend = [
        {"session_id": s.id, "date": s.completed_at, "category": s.category.value, "score": s.overall_score}
        for s in sessions
    ]
    readiness = [
        {"date": r.created_at, "score": r.score}
        for r in db.scalars(
            select(AssessmentResult)
            .where(AssessmentResult.profile_id == pid, AssessmentResult.source == AssessmentSource.readiness)
            .order_by(AssessmentResult.created_at)
        )
    ]
    latest_readiness = None
    last = db.scalar(
        select(AssessmentResult)
        .where(AssessmentResult.profile_id == pid, AssessmentResult.source == AssessmentSource.readiness)
        .order_by(AssessmentResult.id.desc())
        .limit(1)
    )
    if last is not None:
        latest_readiness = last.details

    reminders = [
        {
            "application_id": a.id,
            "company": a.company,
            "title": a.title,
            "next_step": a.next_step,
            "due": a.next_step_due,
            "overdue": a.next_step_due < today,
        }
        for a in db.scalars(
            select(JobApplication)
            .where(
                JobApplication.profile_id == pid,
                JobApplication.next_step_due.is_not(None),
                JobApplication.next_step_due <= today + timedelta(days=7),
                JobApplication.status.not_in([ApplicationStatus.rejected, ApplicationStatus.withdrawn]),
            )
            .order_by(JobApplication.next_step_due)
        )
    ]
    last_wf = db.scalar(
        select(WorkflowExecution)
        .where(WorkflowExecution.user_id == profile.user_id)
        .order_by(WorkflowExecution.created_at.desc())
        .limit(1)
    )
    s = get_settings()
    return {
        "jobs": {"discovered": jobs_discovered, "relevant": relevant, "saved": saved, "demo": demo_jobs},
        "applications": applications,
        "skill_gaps": {
            **gap_counts,
            "total": len(gaps),
            "completion": round(gap_counts["matched"] / len(gaps), 3) if gaps else 0.0,
            "history": history,
        },
        "roadmap": roadmap,
        "upcoming_milestones": upcoming,
        "overdue_tasks": overdue_tasks,
        "interviews": {"completed": len(sessions), "trend": interview_trend},
        "readiness": {"latest": latest_readiness, "trend": readiness},
        "recent_improvements": improvements,
        "remaining_priorities": [
            {"skill": g.skill, "status": g.status.value, "priority": g.priority_score}
            for g in gaps
            if g.status != GapStatus.matched
        ][:5],
        "reminders": reminders,
        "last_workflow": {
            "id": last_wf.id,
            "kind": last_wf.kind.value,
            "status": last_wf.status.value,
            "created_at": last_wf.created_at,
        }
        if last_wf
        else None,
        "modes": {"demo_mode": s.demo_mode, "llm_provider": s.llm_provider, "job_providers": s.job_providers},
    }


def _improvements(db: Session, profile_id: int) -> list[dict]:
    """Skills whose status improved between the two most recent gap snapshots."""
    snaps = list(
        db.execute(
            select(SkillGap.snapshot_id, func.min(SkillGap.created_at).label("c"))
            .where(SkillGap.profile_id == profile_id)
            .group_by(SkillGap.snapshot_id)
            .order_by(func.min(SkillGap.created_at).desc())
            .limit(2)
        ).all()
    )
    if len(snaps) < 2:
        return []
    rank = {GapStatus.missing: 0, GapStatus.partial: 1, GapStatus.matched: 2}
    new = {g.skill: g for g in db.scalars(select(SkillGap).where(SkillGap.snapshot_id == snaps[0][0]))}
    old = {g.skill: g for g in db.scalars(select(SkillGap).where(SkillGap.snapshot_id == snaps[1][0]))}
    out = []
    for skill, g in new.items():
        o = old.get(skill)
        if o and (rank[g.status] > rank[o.status] or g.evidence_strength > o.evidence_strength + 0.05):
            out.append(
                {
                    "skill": skill,
                    "from": o.status.value,
                    "to": g.status.value,
                    "evidence_from": o.evidence_strength,
                    "evidence_to": g.evidence_strength,
                }
            )
    return out
