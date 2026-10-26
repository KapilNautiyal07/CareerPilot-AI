"""Job discovery: search/filter, details with match explanation, bookmarks and refresh."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.agents.job_research_agent import JobResearchAgent
from app.agents.resume_agent import ResumeAnalysisAgent
from app.api.deps import current_profile, rate_limited
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.db.session import get_db
from app.db.types import cosine_distance
from app.models import CandidateProfile, JobApplication, JobListing, JobMatch, JobSkillRequirement, Resume
from app.models.enums import ApplicationStatus, RequirementLevel
from app.repositories import career as repo
from app.schemas.agents import ResumeAnalysis
from app.schemas.api import JobDetailOut, JobMatchOut, JobOut, Page
from app.services import job_parsing as jp
from app.services.embeddings import embed
from app.services.llm import get_llm
from app.tools.job_providers import build_providers

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _availability(job: JobListing) -> str:
    if job.is_demo:
        return "DEMO listing (fictional company, mock provider). Not a real opening."
    checked = job.last_checked_at.strftime("%Y-%m-%d %H:%M UTC")
    note = f"Listed in {job.company}'s public {job.source.title()} job feed as of {checked}."
    flags = jp.stale_flags(job.quality_flags or [], job.last_checked_at, False, get_settings().job_stale_after_days)
    if "stale" in flags:
        note += " It has not been re-confirmed in the feed recently and may be closed."
    elif "long_open" in flags:
        note += " The role has been open for a long time."
    return note + " Confirm on the company's site before applying."


def _job_out(job: JobListing, match: JobMatch | None, app_status: str | None, cls: type = JobOut, **extra) -> JobOut:
    reqs = job.requirements
    return cls(
        id=job.id,
        source=job.source,
        title=job.title,
        company=job.company,
        location=job.location,
        work_arrangement=job.work_arrangement.value,
        employment_type=job.employment_type,
        seniority=job.seniority.value,
        min_years_experience=job.min_years_experience,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        application_url=job.application_url,
        source_url=job.source_url,
        posted_at=job.posted_at,
        retrieved_at=job.retrieved_at,
        last_checked_at=job.last_checked_at,
        is_demo=job.is_demo,
        quality_flags=jp.stale_flags(
            job.quality_flags or [], job.last_checked_at, job.is_demo, get_settings().job_stale_after_days
        ),
        required_skills=[r.skill for r in reqs if r.level == RequirementLevel.required],
        preferred_skills=[r.skill for r in reqs if r.level == RequirementLevel.preferred],
        is_saved=bool(match and match.is_saved),
        application_status=app_status,
        match=JobMatchOut(
            score=match.score,
            skill_score=match.skill_score,
            experience_score=match.experience_score,
            preference_score=match.preference_score,
            details=match.explanation.get("details", []),
            notes=match.explanation.get("notes", []),
            computed_at=match.computed_at,
        )
        if match
        else None,
        **extra,
    )


@router.get("", response_model=Page[JobOut])
def search_jobs(
    q: str | None = Query(default=None, max_length=200, description="Free text; ranked semantically on PostgreSQL"),
    skills: list[str] = Query(default=[]),
    location: str | None = Query(default=None, max_length=120),
    arrangement: str | None = Query(default=None, pattern="^(remote|hybrid|onsite)$"),
    seniority: str | None = Query(default=None, max_length=20),
    max_years: float | None = Query(default=None, ge=0, le=40),
    min_score: float | None = Query(default=None, ge=0, le=100),
    saved: bool = False,
    include_stale: bool = True,
    sort: str = Query(default="match", pattern="^(match|recent|relevance)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> Page[JobOut]:
    stmt = select(JobListing, JobMatch).outerjoin(
        JobMatch, (JobMatch.job_id == JobListing.id) & (JobMatch.profile_id == profile.id)
    )
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(JobListing.title).like(like),
                func.lower(JobListing.company).like(like),
                func.lower(JobListing.description).like(like),
            )
        )
    for skill in skills:
        stmt = stmt.where(
            JobListing.id.in_(select(JobSkillRequirement.job_id).where(JobSkillRequirement.skill == skill))
        )
    if location:
        stmt = stmt.where(func.lower(JobListing.location).like(f"%{location.lower()}%"))
    if arrangement:
        stmt = stmt.where(JobListing.work_arrangement == arrangement)
    if seniority:
        stmt = stmt.where(JobListing.seniority == seniority)
    if max_years is not None:
        stmt = stmt.where(or_(JobListing.min_years_experience.is_(None), JobListing.min_years_experience <= max_years))
    if min_score is not None:
        stmt = stmt.where(JobMatch.score >= min_score)
    if saved:
        stmt = stmt.where(JobMatch.is_saved.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    if sort == "relevance" and q and db.get_bind().dialect.name == "postgresql":
        stmt = stmt.order_by(cosine_distance(JobListing.embedding, embed(q)))
    elif sort == "recent":
        stmt = stmt.order_by(JobListing.posted_at.desc().nulls_last())
    else:
        stmt = stmt.order_by(JobMatch.score.desc().nulls_last(), JobListing.id.desc())
    rows = db.execute(
        stmt.options(selectinload(JobListing.requirements)).offset((page - 1) * page_size).limit(page_size)
    ).all()
    if not include_stale:
        stale_days = get_settings().job_stale_after_days
        rows = [
            r
            for r in rows
            if "stale" not in jp.stale_flags(r[0].quality_flags or [], r[0].last_checked_at, r[0].is_demo, stale_days)
        ]
    apps = dict(
        db.execute(
            select(JobApplication.job_id, JobApplication.status).where(JobApplication.profile_id == profile.id)
        ).all()
    )
    items = [_job_out(j, m, apps[j.id].value if j.id in apps else None) for j, m in rows]
    return Page(items=items, total=total or 0, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobDetailOut)
def job_detail(
    job_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> JobDetailOut:
    job = db.scalar(select(JobListing).options(selectinload(JobListing.requirements)).where(JobListing.id == job_id))
    if job is None:
        raise NotFoundError("Job not found")
    match = db.scalar(select(JobMatch).where(JobMatch.profile_id == profile.id, JobMatch.job_id == job.id))
    app = db.scalar(
        select(JobApplication.status).where(JobApplication.profile_id == profile.id, JobApplication.job_id == job.id)
    )
    tailoring = None
    resume = db.get(Resume, profile.active_resume_id) if profile.active_resume_id else None
    if resume is not None and resume.analysis:
        tailoring = (
            ResumeAnalysisAgent(get_llm())
            .tailor(ResumeAnalysis.model_validate(resume.analysis), job.title, repo.job_to_normalized(job).requirements)
            .model_dump(mode="json")
        )
    return _job_out(
        job,
        match,
        app.value if app else None,
        JobDetailOut,
        description=job.description,
        requirements=[
            {"skill": r.skill, "level": r.level.value, "evidence_text": r.evidence_text} for r in job.requirements
        ],
        tailoring=tailoring,
        availability_note=_availability(job),
    )


def _set_saved(db: Session, profile: CandidateProfile, job_id: int, saved: bool) -> dict:
    job = db.get(JobListing, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    match = db.scalar(select(JobMatch).where(JobMatch.profile_id == profile.id, JobMatch.job_id == job_id))
    if match is None:
        # Score on demand so a bookmarked job always has an explanation.
        have = repo.candidate_skill_map(db, profile.id)
        prefs = repo.preferences_for(repo.get_or_create_preference(db, profile.user_id))
        s = get_settings()
        scored = JobResearchAgent.rank(
            [(job.id, repo.job_to_normalized(job))],
            have,
            profile.years_experience,
            prefs,
            s.job_stale_after_days,
            profile.target_role,
        )
        repo.save_matches(db, profile.id, scored)
        match = db.scalar(select(JobMatch).where(JobMatch.profile_id == profile.id, JobMatch.job_id == job_id))
    match.is_saved = saved
    if (
        saved
        and db.scalar(
            select(JobApplication.id).where(JobApplication.profile_id == profile.id, JobApplication.job_id == job_id)
        )
        is None
    ):
        db.add(
            JobApplication(
                profile_id=profile.id,
                job_id=job.id,
                company=job.company,
                title=job.title,
                job_url=job.application_url,
                status=ApplicationStatus.saved,
                status_history=[{"status": "saved", "at": datetime.now(UTC).isoformat()}],
            )
        )
    db.commit()
    return {"job_id": job_id, "is_saved": saved}


@router.post("/{job_id}/save", response_model=dict)
def save_job(job_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)) -> dict:
    return _set_saved(db, profile, job_id, True)


@router.delete("/{job_id}/save", response_model=dict)
def unsave_job(
    job_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> dict:
    return _set_saved(db, profile, job_id, False)


@router.post("/refresh", response_model=dict)
def refresh_jobs(
    profile: CandidateProfile = Depends(current_profile),
    _rl: None = Depends(rate_limited("jobs", "rate_limit_expensive_per_minute")),
    db: Session = Depends(get_db),
) -> dict:
    """Run the Job Research Agent now (without the full career workflow) and re-rank matches."""
    if not profile.target_role:
        raise AppError("Set a target role first", code="missing_target_role")
    s = get_settings()
    providers = build_providers(
        s.job_providers,
        greenhouse_boards=s.greenhouse_boards,
        lever_companies=s.lever_companies,
        timeout=s.job_http_timeout_seconds,
        stale_after_days=s.job_stale_after_days,
    )
    agent = JobResearchAgent(providers, max_workers=s.workflow_workers)
    pref = repo.get_or_create_preference(db, profile.user_id)
    discovery = agent.discover(profile.target_role, list(pref.preferred_locations or []))
    stored = repo.upsert_jobs(db, discovery.jobs)
    ranked = agent.rank(
        stored,
        repo.candidate_skill_map(db, profile.id),
        profile.years_experience,
        repo.preferences_for(pref),
        s.job_stale_after_days,
        profile.target_role,
    )
    repo.save_matches(db, profile.id, ranked)
    db.commit()
    return {
        "providers_used": discovery.providers_used,
        "provider_errors": discovery.errors,
        "fetched": discovery.fetched,
        "after_dedupe": len(discovery.jobs),
        "ranked": len(ranked),
        "demo_data": any(j.is_demo for j in discovery.jobs),
    }
