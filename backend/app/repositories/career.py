"""Data access for profiles, skills, jobs, gaps, roadmaps and assessments.

Every function that reads user data takes a profile id resolved from the authenticated user
(see api/deps.py), so ownership is enforced at the query level, not by callers remembering to.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.db.types import cosine_distance
from app.models import (
    AssessmentResult,
    CandidateProfile,
    ExtractedSkill,
    JobListing,
    JobMatch,
    JobSkillRequirement,
    LearningResource,
    LearningRoadmap,
    ProjectExperience,
    Resume,
    RoadmapTask,
    SkillGap,
    User,
    UserPreference,
    WorkExperience,
)
from app.models.enums import (
    AssessmentSource,
    EvidenceLevel,
    ResourceVerification,
    ResumeStatus,
    RoadmapStatus,
    SkillSource,
    TaskStatus,
)
from app.schemas.agents import (
    JobMatchScore,
    NormalizedJob,
    ResumeAnalysis,
    RoadmapPlan,
    SkillGapReport,
    SkillRequirement,
)
from app.services.embeddings import cosine, embed
from app.services.matching import CandidateSkill, Preferences

DATA_DIR = Path(__file__).resolve().parents[1] / "tools" / "data"


# ---------------------------------------------------------------------- profile
def get_or_create_profile(db: Session, user_id: int) -> CandidateProfile:
    profile = db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
    if profile is None:
        profile = CandidateProfile(user_id=user_id)
        db.add(profile)
        db.flush()
    return profile


def get_or_create_preference(db: Session, user_id: int) -> UserPreference:
    pref = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if pref is None:
        pref = UserPreference(user_id=user_id, preferred_locations=[], work_arrangements=[], interview_categories=[])
        db.add(pref)
        db.flush()
    return pref


def preferences_for(pref: UserPreference) -> Preferences:
    return Preferences(
        locations=list(pref.preferred_locations or []),
        arrangements=list(pref.work_arrangements or []),
        salary_min=pref.salary_min,
        salary_currency=pref.salary_currency,
    )


def latest_assessments(db: Session, profile_id: int) -> dict[str, float]:
    """Latest interview assessment score per skill."""
    sub = (
        select(AssessmentResult.skill, func.max(AssessmentResult.id).label("mid"))
        .where(
            AssessmentResult.profile_id == profile_id,
            AssessmentResult.source == AssessmentSource.interview,
            AssessmentResult.skill.is_not(None),
        )
        .group_by(AssessmentResult.skill)
        .subquery()
    )
    rows = db.execute(
        select(AssessmentResult.skill, AssessmentResult.score).join(sub, AssessmentResult.id == sub.c.mid)
    ).all()
    return {s: float(v) for s, v in rows}


def candidate_skill_map(db: Session, profile_id: int) -> dict[str, CandidateSkill]:
    assessed = latest_assessments(db, profile_id)
    out: dict[str, CandidateSkill] = {}
    for s in db.scalars(select(ExtractedSkill).where(ExtractedSkill.profile_id == profile_id)):
        out[s.skill] = CandidateSkill(s.skill, s.evidence_level, assessed.get(s.skill), list(s.evidence or []))
    for skill, score in assessed.items():
        if skill not in out:
            out[skill] = CandidateSkill(skill, EvidenceLevel.assessed, score, [])
    return out


def apply_resume_analysis(db: Session, profile: CandidateProfile, resume: Resume, analysis: ResumeAnalysis) -> None:
    """Replace machine-extracted data from this resume. User-confirmed or user-added skills are kept."""
    db.execute(
        delete(ExtractedSkill).where(
            ExtractedSkill.profile_id == profile.id,
            ExtractedSkill.user_confirmed.is_(False),
            ExtractedSkill.source != SkillSource.user,
        )
    )
    kept = set(db.scalars(select(ExtractedSkill.skill).where(ExtractedSkill.profile_id == profile.id)))
    for s in analysis.skills:
        if s.skill in kept:
            continue
        db.add(
            ExtractedSkill(
                profile_id=profile.id,
                resume_id=resume.id,
                skill=s.skill,
                category=s.category,
                source=s.source,
                evidence_level=s.evidence_level,
                evidence=s.evidence,
                years=s.years,
                confidence=s.confidence,
            )
        )
    db.execute(delete(WorkExperience).where(WorkExperience.profile_id == profile.id))
    for i, e in enumerate(analysis.experiences):
        db.add(
            WorkExperience(
                profile_id=profile.id,
                resume_id=resume.id,
                title=e.title,
                company=e.company,
                location=e.location,
                start_date=e.start_date,
                end_date=e.end_date,
                is_current=e.is_current,
                highlights=e.highlights,
                skills=e.skills,
                order_index=i,
            )
        )
    db.execute(delete(ProjectExperience).where(ProjectExperience.profile_id == profile.id))
    for p in analysis.projects:
        db.add(
            ProjectExperience(
                profile_id=profile.id,
                resume_id=resume.id,
                name=p.name,
                description=p.description,
                technologies=p.technologies,
                url=p.url,
                highlights=p.highlights,
            )
        )
    profile.education = [e.model_dump() for e in analysis.education]
    profile.certifications = [c.model_dump() for c in analysis.certifications]
    profile.achievements = analysis.achievements
    if analysis.total_years_experience is not None:
        profile.years_experience = analysis.total_years_experience
    profile.headline = profile.headline or (analysis.headline or "")
    profile.location = profile.location or (analysis.location or "")
    profile.summary = analysis.summary
    profile.active_resume_id = resume.id
    resume.status = ResumeStatus.analyzed
    resume.analysis = analysis.model_dump(mode="json")
    resume.security_flags = analysis.security_flags
    resume.analyzed_at = datetime.now(UTC)


# ---------------------------------------------------------------------- jobs
def _req_rows(job: NormalizedJob) -> list[JobSkillRequirement]:
    return [JobSkillRequirement(skill=r.skill, level=r.level, evidence_text=r.evidence_text) for r in job.requirements]


def upsert_jobs(db: Session, jobs: list[NormalizedJob]) -> list[tuple[int, NormalizedJob]]:
    out: list[tuple[int, NormalizedJob]] = []
    from app.services.job_parsing import fingerprint

    for job in jobs:
        row = db.scalar(
            select(JobListing)
            .options(selectinload(JobListing.requirements))
            .where(JobListing.source == job.source, JobListing.external_id == job.external_id)
        )
        fields = job.model_dump(exclude={"requirements"})
        fields["fingerprint"] = fingerprint(job.company, job.title, job.location)
        fields["last_checked_at"] = job.retrieved_at
        fields["embedding"] = embed(f"{job.title} {job.company} {job.description[:3000]}")
        if row is None:
            row = JobListing(**fields)
            row.requirements = _req_rows(job)
            db.add(row)
        else:
            fields.pop("retrieved_at", None)  # keep first-seen time; last_checked_at tracks refreshes
            for k, v in fields.items():
                setattr(row, k, v)
            row.requirements.clear()
            db.flush()
            row.requirements.extend(_req_rows(job))
        db.flush()
        out.append((row.id, job))
    return out


def job_to_normalized(row: JobListing) -> NormalizedJob:
    return NormalizedJob(
        source=row.source,
        external_id=row.external_id,
        title=row.title,
        company=row.company,
        location=row.location,
        work_arrangement=row.work_arrangement,
        employment_type=row.employment_type,
        seniority=row.seniority,
        min_years_experience=row.min_years_experience,
        salary_min=row.salary_min,
        salary_max=row.salary_max,
        salary_currency=row.salary_currency,
        description=row.description,
        application_url=row.application_url,
        source_url=row.source_url,
        posted_at=_aware(row.posted_at),
        retrieved_at=_aware(row.retrieved_at),
        requirements=[
            SkillRequirement(skill=r.skill, level=r.level, evidence_text=r.evidence_text) for r in row.requirements
        ],
        quality_flags=list(row.quality_flags or []),
        is_demo=row.is_demo,
    )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def save_matches(db: Session, profile_id: int, matches: list[JobMatchScore]) -> None:
    existing = {m.job_id: m for m in db.scalars(select(JobMatch).where(JobMatch.profile_id == profile_id))}
    now = datetime.now(UTC)
    for m in matches:
        row = existing.get(m.job_id)
        data = dict(
            score=m.score,
            skill_score=m.skill_score,
            experience_score=m.experience_score,
            preference_score=m.preference_score,
            computed_at=now,
            explanation={
                "details": [d.model_dump(mode="json") for d in m.details],
                "notes": m.notes,
                "recency_score": m.recency_score,
                "role_score": m.role_score,
            },
        )
        if row is None:
            db.add(JobMatch(profile_id=profile_id, job_id=m.job_id, **data))
        else:
            for k, v in data.items():
                setattr(row, k, v)
    db.flush()


# ---------------------------------------------------------------------- resources (RAG over the catalog)
def seed_resources(db: Session) -> int:
    items = json.loads((DATA_DIR / "resources.json").read_text(encoding="utf-8"))
    existing = {r.url: r for r in db.scalars(select(LearningResource))}
    added = 0
    for it in items:
        vec = embed(f"{it['title']} {' '.join(it['skills'])} {it['description']}")
        row = existing.get(it["url"])
        if row is None:
            db.add(LearningResource(**it, embedding=vec))
            added += 1
        else:
            for k, v in it.items():
                setattr(row, k, v)
            row.embedding = vec
    db.flush()
    return added


def resource_dict(r: LearningResource) -> dict:
    return {
        "id": r.id,
        "title": r.title,
        "url": r.url,
        "provider": r.provider,
        "resource_type": r.resource_type,
        "skills": r.skills,
        "level": r.level,
        "is_free": r.is_free,
        "verification_status": r.verification_status.value,
        "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
    }


def retrieve_resources(db: Session, skills: list[str], context: str = "", per_skill: int = 5) -> dict[str, list[dict]]:
    """Hybrid retrieval: hard filter on the skill tag, then rank by semantic similarity to "<skill> <context>".

    On PostgreSQL the tag filter runs in SQL (JSONB containment) before the pgvector ordering, so a
    skill's resources are never crowded out by nearer resources for other skills as the catalog grows.
    Unreachable links are never returned; links verified reachable come first. `per_skill` returns a
    few extra candidates so the planner can choose by level and task type.
    """
    is_pg = db.get_bind().dialect.name == "postgresql"
    out: dict[str, list[dict]] = {}
    all_rows: list[LearningResource] | None = None
    for skill in skills:
        q = embed(f"{skill} {context}")
        if is_pg:
            rows = list(
                db.scalars(
                    select(LearningResource)
                    .where(cast(LearningResource.skills, JSONB).contains([skill]))
                    .where(LearningResource.verification_status != ResourceVerification.unreachable)
                    .order_by(cosine_distance(LearningResource.embedding, q))
                    .limit(per_skill * 4)
                )
            )
        else:
            if all_rows is None:
                all_rows = list(db.scalars(select(LearningResource)))
            rows = sorted(
                (
                    r
                    for r in all_rows
                    if skill in (r.skills or []) and r.verification_status != ResourceVerification.unreachable
                ),
                key=lambda r: -cosine(q, r.embedding or [0.0] * len(q)),
            )
        rows.sort(key=lambda r: r.verification_status != ResourceVerification.reachable)  # stable
        out[skill] = [resource_dict(r) for r in rows[:per_skill]]
    return out


# ---------------------------------------------------------------------- gaps
def save_gap_snapshot(db: Session, profile_id: int, report: SkillGapReport) -> str:
    snapshot = str(uuid.uuid4())
    db.execute(
        update(SkillGap)
        .where(SkillGap.profile_id == profile_id, SkillGap.is_current.is_(True))
        .values(is_current=False)
    )
    for g in report.items:
        db.add(
            SkillGap(
                profile_id=profile_id,
                snapshot_id=snapshot,
                is_current=True,
                target_role=report.target_role,
                skill=g.skill,
                category=g.category,
                status=g.status,
                requirement_level=g.requirement_level,
                demand_share=g.demand_share,
                evidence_strength=g.evidence_strength,
                evidence=g.evidence,
                priority_score=g.priority_score,
                priority_components=g.components.model_dump(),
                estimated_hours=g.estimated_hours,
                transferable_from=g.transferable_from,
                recommendation=g.recommendation,
            )
        )
    db.flush()
    return snapshot


# ---------------------------------------------------------------------- roadmap
def active_roadmap(db: Session, profile_id: int) -> LearningRoadmap | None:
    return db.scalar(
        select(LearningRoadmap)
        .options(selectinload(LearningRoadmap.tasks))
        .where(LearningRoadmap.profile_id == profile_id, LearningRoadmap.status == RoadmapStatus.active)
        .order_by(LearningRoadmap.id.desc())
    )


def _task_rows(plan: RoadmapPlan, resources: dict[int, LearningResource]) -> list[RoadmapTask]:
    rows = []
    for w in plan.weeks:
        for i, t in enumerate(w.tasks):
            task = RoadmapTask(
                week_index=w.index,
                scheduled_date=t.scheduled_date,
                due_date=t.due_date,
                order_index=i,
                title=t.title,
                description=t.description,
                task_type=t.task_type,
                skill=t.skill,
                estimated_hours=t.hours,
                origin=t.origin,
            )
            task.resources = [resources[r] for r in t.resource_ids if r in resources]
            rows.append(task)
    return rows


def _resource_index(db: Session, plan: RoadmapPlan) -> dict[int, LearningResource]:
    ids = {r for w in plan.weeks for t in w.tasks for r in t.resource_ids}
    if not ids:
        return {}
    return {r.id: r for r in db.scalars(select(LearningResource).where(LearningResource.id.in_(ids)))}


def save_new_roadmap(
    db: Session,
    profile_id: int,
    plan: RoadmapPlan,
    *,
    execution_id: str | None,
    evaluation: dict | None,
    revisions: int,
) -> LearningRoadmap:
    prev = active_roadmap(db, profile_id)
    version = 1
    if prev is not None:
        prev.status = RoadmapStatus.superseded
        version = prev.version + 1
    roadmap = LearningRoadmap(
        profile_id=profile_id,
        workflow_execution_id=execution_id,
        status=RoadmapStatus.active,
        version=version,
        title=plan.title,
        target_role=plan.target_role,
        start_date=plan.start_date,
        end_date=plan.end_date,
        weekly_hours=plan.weekly_hours,
        study_days_per_week=plan.study_days_per_week,
        summary=_summary(plan, evaluation),
        revision_log=[
            {
                "at": datetime.now(UTC).isoformat(),
                "version": version,
                "reason": "generated",
                "revisions_before_valid": revisions,
            }
        ],
    )
    roadmap.tasks = _task_rows(plan, _resource_index(db, plan))
    db.add(roadmap)
    db.flush()
    return roadmap


def apply_replan(
    db: Session, roadmap: LearningRoadmap, plan: RoadmapPlan, *, reason: str, evaluation: dict | None, changes: dict
) -> LearningRoadmap:
    """Keep history (completed/skipped tasks), supersede pending ones, insert the new schedule."""
    for t in roadmap.tasks:
        if t.status == TaskStatus.pending:
            t.status = TaskStatus.superseded
    roadmap.tasks.extend(_task_rows(plan, _resource_index(db, plan)))
    roadmap.version += 1
    roadmap.summary = _summary(plan, evaluation)
    roadmap.revision_log = [
        *roadmap.revision_log,
        {"at": datetime.now(UTC).isoformat(), "version": roadmap.version, "reason": reason, **changes},
    ]
    db.flush()
    return roadmap


def _summary(plan: RoadmapPlan, evaluation: dict | None) -> dict:
    return {
        "capacity_hours": plan.capacity_hours,
        "planned_hours": plan.planned_hours,
        "covered_skills": plan.covered_skills,
        "uncovered_gaps": plan.uncovered_gaps,
        "assumptions": plan.assumptions,
        "milestones": [
            {
                "week": w.index,
                "start": w.start.isoformat(),
                "end": w.end.isoformat(),
                "milestone": w.milestone,
                "focus": w.focus,
                "planned_hours": w.planned_hours,
            }
            for w in plan.weeks
        ],
        "evaluation": evaluation,
    }


def roadmap_progress(roadmap: LearningRoadmap | None) -> tuple[float, float] | None:
    if roadmap is None:
        return None
    live = [t for t in roadmap.tasks if t.status != TaskStatus.superseded]
    done = sum(t.estimated_hours for t in live if t.status == TaskStatus.completed)
    return round(done, 1), round(sum(t.estimated_hours for t in live), 1)


# ---------------------------------------------------------------------- readiness history
def last_readiness(db: Session, profile_id: int) -> float | None:
    return db.scalar(
        select(AssessmentResult.score)
        .where(AssessmentResult.profile_id == profile_id, AssessmentResult.source == AssessmentSource.readiness)
        .order_by(AssessmentResult.id.desc())
        .limit(1)
    )


def record_readiness(db: Session, profile_id: int, readiness: dict) -> None:
    db.add(
        AssessmentResult(
            profile_id=profile_id,
            source=AssessmentSource.readiness,
            skill=None,
            competency="overall_readiness",
            score=readiness["score"],
            details=readiness,
        )
    )


def weeks_until(deadline: date | None, today: date | None = None) -> float | None:
    if deadline is None:
        return None
    return max(0.5, round(((deadline - (today or date.today())).days + 1) / 7, 1))


def user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user
