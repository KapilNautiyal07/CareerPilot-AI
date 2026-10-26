"""Resume upload, analysis, review and resume-to-job comparison."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.resume_agent import ResumeAnalysisAgent
from app.api.deps import current_profile, rate_limited
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import get_logger, log_ctx
from app.db.session import get_db
from app.models import CandidateProfile, JobListing, Resume
from app.models.enums import ResumeStatus
from app.repositories import career as repo
from app.schemas.agents import ResumeAnalysis
from app.schemas.api import CompareIn, ResumeOut
from app.services.document_text import extract_text, sniff_type
from app.services.job_parsing import extract_requirements
from app.services.llm import get_llm
from app.services.matching import score_skills
from app.services.storage import get_storage

router = APIRouter(prefix="/resumes", tags=["resumes"])
logger = get_logger(__name__)
SAMPLE = Path(__file__).resolve().parents[2] / "demo" / "sample_resume.txt"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


def _out(r: Resume, profile: CandidateProfile, with_analysis: bool = True) -> ResumeOut:
    out = ResumeOut.model_validate(r)
    out.is_active = profile.active_resume_id == r.id
    out.analysis = r.analysis if with_analysis else None
    return out


def _owned(db: Session, profile: CandidateProfile, resume_id: int) -> Resume:
    r = db.get(Resume, resume_id)
    if r is None or r.profile_id != profile.id:
        raise NotFoundError("Resume not found")
    return r


def _analyze_and_apply(db: Session, profile: CandidateProfile, resume: Resume) -> None:
    try:
        analysis = ResumeAnalysisAgent(get_llm()).analyze(resume.raw_text)
    except Exception as exc:
        resume.status, resume.error = (
            ResumeStatus.failed,
            "Analysis failed; you can retry or edit your profile manually.",
        )
        db.commit()
        logger.exception("Resume analysis failed", extra=log_ctx(resume_id=resume.id))
        raise AppError("Resume analysis failed", code="analysis_failed") from exc
    repo.apply_resume_analysis(db, profile, resume, analysis)
    db.commit()


@router.post("", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
def upload_resume(
    file: UploadFile = File(...),
    profile: CandidateProfile = Depends(current_profile),
    _rl: None = Depends(rate_limited("resume", "rate_limit_expensive_per_minute")),
    db: Session = Depends(get_db),
) -> ResumeOut:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"File exceeds the {get_settings().max_upload_mb} MB limit")
    if not data:
        raise AppError("The uploaded file is empty", code="empty_file")
    name = Path(file.filename or "resume").name[:255]
    mime = sniff_type(name, data)  # magic bytes + extension, never trusts the client content-type
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(Resume).where(Resume.profile_id == profile.id, Resume.sha256 == digest))
    if existing is not None:
        profile.active_resume_id = existing.id
        db.commit()
        return _out(existing, profile)
    text = extract_text(data, mime)
    key = get_storage().save(data, Path(name).suffix.lower())
    resume = Resume(
        profile_id=profile.id,
        original_filename=name,
        content_type=mime,
        storage_key=key,
        size_bytes=len(data),
        sha256=digest,
        raw_text=text,
        status=ResumeStatus.parsed,
    )
    db.add(resume)
    db.flush()
    _analyze_and_apply(db, profile, resume)
    return _out(resume, profile)


@router.post("/sample", response_model=ResumeOut, status_code=status.HTTP_201_CREATED)
def load_sample_resume(
    profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> ResumeOut:
    """Demo mode: analyze a bundled fictional resume (clearly labeled) instead of uploading one."""
    text = SAMPLE.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    resume = db.scalar(select(Resume).where(Resume.profile_id == profile.id, Resume.sha256 == digest))
    if resume is None:
        key = get_storage().save(text.encode(), ".txt")
        resume = Resume(
            profile_id=profile.id,
            original_filename="DEMO sample resume (Priya Sharma, fictional).txt",
            content_type="text/plain",
            storage_key=key,
            size_bytes=len(text),
            sha256=digest,
            raw_text=text,
            status=ResumeStatus.parsed,
        )
        db.add(resume)
        db.flush()
    _analyze_and_apply(db, profile, resume)
    return _out(resume, profile)


@router.get("", response_model=list[ResumeOut])
def list_resumes(
    profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> list[ResumeOut]:
    rows = db.scalars(select(Resume).where(Resume.profile_id == profile.id).order_by(Resume.uploaded_at.desc()))
    return [_out(r, profile, with_analysis=False) for r in rows]


@router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> ResumeOut:
    return _out(_owned(db, profile, resume_id), profile)


@router.post("/{resume_id}/reanalyze", response_model=ResumeOut)
def reanalyze(
    resume_id: int,
    profile: CandidateProfile = Depends(current_profile),
    _rl: None = Depends(rate_limited("resume", "rate_limit_expensive_per_minute")),
    db: Session = Depends(get_db),
) -> ResumeOut:
    r = _owned(db, profile, resume_id)
    _analyze_and_apply(db, profile, r)
    return _out(r, profile)


@router.post("/{resume_id}/reviewed", response_model=ResumeOut)
def mark_reviewed(
    resume_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> ResumeOut:
    from datetime import UTC, datetime

    r = _owned(db, profile, resume_id)
    r.reviewed_at = datetime.now(UTC)
    db.commit()
    return _out(r, profile)


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> None:
    r = _owned(db, profile, resume_id)
    key = r.storage_key
    if profile.active_resume_id == r.id:
        profile.active_resume_id = None
    db.delete(r)
    db.commit()
    get_storage().delete(key)


@router.post("/{resume_id}/compare", response_model=dict)
def compare_to_job(
    resume_id: int, body: CompareIn, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> dict:
    """Compare the analyzed resume with a saved job or a pasted job description."""
    r = _owned(db, profile, resume_id)
    if not r.analysis:
        raise AppError("Analyze the resume first", code="not_analyzed")
    analysis = ResumeAnalysis.model_validate(r.analysis)
    if body.job_id is not None:
        job = db.scalar(
            select(JobListing).options(selectinload(JobListing.requirements)).where(JobListing.id == body.job_id)
        )
        if job is None:
            raise NotFoundError("Job not found")
        title, reqs = job.title, repo.job_to_normalized(job).requirements
    elif body.job_description:
        title, reqs = body.job_title, extract_requirements(body.job_description)
    else:
        raise AppError("Provide job_id or job_description", code="validation_error")
    have = repo.candidate_skill_map(db, profile.id)
    skill_score, details = score_skills(reqs, have)
    tailoring = ResumeAnalysisAgent(get_llm()).tailor(analysis, title, reqs)
    return {
        "job_title": title,
        "skill_score": skill_score,
        "details": [d.model_dump(mode="json") for d in details],
        "tailoring": tailoring.model_dump(mode="json"),
        "requirements_found": len(reqs),
    }
