"""Skill gaps, roadmap, task completion and replanning."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_profile, current_user, rate_limited
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.graph.runner import runner
from app.models import CandidateProfile, LearningRoadmap, RoadmapTask, SkillGap, User
from app.models.enums import GapStatus, RoadmapStatus, TaskStatus, WorkflowKind
from app.repositories import career as repo
from app.repositories.career import resource_dict
from app.schemas.api import RoadmapOut, TaskOut, TaskPatch

router = APIRouter(tags=["learning"])


def _gap_dict(g: SkillGap) -> dict:
    return {
        "id": g.id,
        "skill": g.skill,
        "category": g.category,
        "status": g.status.value,
        "requirement_level": g.requirement_level.value,
        "demand_share": g.demand_share,
        "evidence_strength": g.evidence_strength,
        "evidence": g.evidence,
        "priority_score": g.priority_score,
        "priority_components": g.priority_components,
        "estimated_hours": g.estimated_hours,
        "transferable_from": g.transferable_from,
        "recommendation": g.recommendation,
    }


@router.get("/gaps", response_model=dict)
def current_gaps(profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)) -> dict:
    rows = list(
        db.scalars(
            select(SkillGap)
            .where(SkillGap.profile_id == profile.id, SkillGap.is_current.is_(True))
            .order_by(SkillGap.priority_score.desc())
        )
    )
    counts = {s.value: sum(1 for g in rows if g.status == s) for s in GapStatus}
    return {
        "target_role": rows[0].target_role if rows else profile.target_role,
        "snapshot_id": rows[0].snapshot_id if rows else None,
        "computed_at": rows[0].created_at if rows else None,
        "counts": counts,
        "items": [_gap_dict(g) for g in rows],
        "history": gap_history(profile, db),
    }


@router.get("/gaps/history", response_model=list[dict])
def gap_history(profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(SkillGap.snapshot_id, func.min(SkillGap.created_at), SkillGap.status, func.count())
        .where(SkillGap.profile_id == profile.id)
        .group_by(SkillGap.snapshot_id, SkillGap.status)
    ).all()
    snaps: dict[str, dict] = {}
    for sid, created, st, n in rows:
        s = snaps.setdefault(sid, {"snapshot_id": sid, "created_at": created, "matched": 0, "partial": 0, "missing": 0})
        s[st.value] = n
        s["created_at"] = min(s["created_at"], created)
    out = sorted(snaps.values(), key=lambda s: s["created_at"])
    for s in out:
        total = s["matched"] + s["partial"] + s["missing"]
        s["matched_share"] = round(s["matched"] / total, 3) if total else 0.0
    return out


def _task_out(t: RoadmapTask, today: date) -> TaskOut:
    fields = {f: getattr(t, f) for f in TaskOut.model_fields if f not in ("resources", "overdue")}
    return TaskOut(
        **fields,
        resources=[resource_dict(r) for r in t.resources],
        overdue=t.status == TaskStatus.pending and t.due_date < today,
    )


def roadmap_out(rm: LearningRoadmap) -> RoadmapOut:
    today = date.today()
    live = [t for t in rm.tasks if t.status != TaskStatus.superseded]
    # Group by calendar week relative to the roadmap start so replanned tasks sit with the original ones.
    by_week: dict[date, list[RoadmapTask]] = {}
    for t in live:
        offset = (t.scheduled_date - rm.start_date).days // 7 * 7
        by_week.setdefault(rm.start_date + timedelta(days=offset), []).append(t)
    milestones = rm.summary.get("milestones", [])

    def milestone_for(start: date, end: date) -> dict:
        for m in milestones:
            if date.fromisoformat(m["start"]) <= end and date.fromisoformat(m["end"]) >= start:
                return m
        return {}

    week_list = []
    for i, (start, tasks) in enumerate(sorted(by_week.items()), start=1):
        tasks.sort(key=lambda t: (t.scheduled_date, t.order_index))
        end = min(rm.end_date, start + timedelta(days=6))
        hours = sum(t.estimated_hours for t in tasks)
        done = sum(t.estimated_hours for t in tasks if t.status == TaskStatus.completed)
        m = milestone_for(start, end)
        week_list.append(
            {
                "index": i,
                "start": start,
                "end": end,
                "milestone": m.get("milestone") or "Continue the plan",
                "focus": list(dict.fromkeys(t.skill for t in tasks if t.skill)),
                "planned_hours": round(hours, 1),
                "completed_hours": round(done, 1),
                "tasks": [_task_out(t, today) for t in tasks],
            }
        )
    done_h, total_h = repo.roadmap_progress(rm) or (0.0, 0.0)
    progress = {
        "completed_hours": done_h,
        "total_hours": total_h,
        "percent": round(100 * done_h / total_h, 1) if total_h else 0.0,
        "completed_tasks": sum(t.status == TaskStatus.completed for t in live),
        "total_tasks": len(live),
        "overdue_tasks": sum(t.status == TaskStatus.pending and t.due_date < today for t in live),
    }
    return RoadmapOut(
        id=rm.id,
        version=rm.version,
        title=rm.title,
        target_role=rm.target_role,
        start_date=rm.start_date,
        end_date=rm.end_date,
        weekly_hours=rm.weekly_hours,
        study_days_per_week=rm.study_days_per_week,
        summary=rm.summary,
        revision_log=rm.revision_log,
        progress=progress,
        weeks=week_list,
    )


@router.get("/roadmap", response_model=RoadmapOut | None)
def get_roadmap(
    profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> RoadmapOut | None:
    rm = repo.active_roadmap(db, profile.id)
    return roadmap_out(rm) if rm else None


@router.get("/roadmap/today", response_model=list[TaskOut])
def today_tasks(profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)) -> list[TaskOut]:
    rm = repo.active_roadmap(db, profile.id)
    if rm is None:
        return []
    today = date.today()
    tasks = [t for t in rm.tasks if t.status == TaskStatus.pending and t.scheduled_date <= today]
    return [_task_out(t, today) for t in sorted(tasks, key=lambda t: (t.scheduled_date, t.order_index))[:10]]


@router.patch("/roadmap/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int, body: TaskPatch, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> TaskOut:
    task = db.scalar(
        select(RoadmapTask)
        .join(LearningRoadmap)
        .where(RoadmapTask.id == task_id, LearningRoadmap.profile_id == profile.id)
    )
    if task is None or task.roadmap.status != RoadmapStatus.active:
        raise NotFoundError("Task not found")
    if body.status is not None:
        task.status = TaskStatus(body.status)
        task.completed_at = datetime.now(UTC) if task.status == TaskStatus.completed else None
    if body.self_rating is not None:
        task.self_rating = body.self_rating
    if body.notes is not None:
        task.notes = body.notes
    db.commit()
    return _task_out(task, date.today())


@router.post("/roadmap/replan", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
def replan(
    user: User = Depends(current_user),
    profile: CandidateProfile = Depends(current_profile),
    _rl: None = Depends(rate_limited("workflow", "rate_limit_expensive_per_minute")),
) -> dict:
    """Re-evaluate gaps with current progress and assessments, then update the roadmap (async)."""
    if not profile.active_resume_id and not profile.skills:
        raise NotFoundError("Run the career analysis first")
    ex_id = runner.start(WorkflowKind.replan, user.id, profile.id, {"trigger": "manual"})
    return {"execution_id": ex_id}
