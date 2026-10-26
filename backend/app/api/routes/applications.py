"""Application tracker. CareerPilot never submits applications or contacts employers; this only
records what the user did, with status history and next-step reminders."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_profile
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.session import get_db
from app.models import CandidateProfile, JobApplication, JobListing
from app.models.enums import ApplicationStatus
from app.schemas.api import ApplicationIn, ApplicationOut, ApplicationPatch

router = APIRouter(prefix="/applications", tags=["applications"])


def _owned(db: Session, profile: CandidateProfile, app_id: int) -> JobApplication:
    a = db.get(JobApplication, app_id)
    if a is None or a.profile_id != profile.id:
        raise NotFoundError("Application not found")
    return a


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    status_: ApplicationStatus | None = Query(default=None, alias="status"),
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> list[ApplicationOut]:
    stmt = select(JobApplication).where(JobApplication.profile_id == profile.id)
    if status_:
        stmt = stmt.where(JobApplication.status == status_)
    return [ApplicationOut.model_validate(a) for a in db.scalars(stmt.order_by(JobApplication.updated_at.desc()))]


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    body: ApplicationIn, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> ApplicationOut:
    company, title, url = body.company, body.title, body.job_url
    if body.job_id is not None:
        job = db.get(JobListing, body.job_id)
        if job is None:
            raise NotFoundError("Job not found")
        if db.scalar(
            select(JobApplication.id).where(JobApplication.profile_id == profile.id, JobApplication.job_id == job.id)
        ):
            raise ConflictError("This job is already in your tracker")
        company, title, url = company or job.company, title or job.title, url or job.application_url
    if not company or not title:
        raise AppError("company and title are required when no job_id is given", code="validation_error")
    now = datetime.now(UTC).isoformat()
    a = JobApplication(
        profile_id=profile.id,
        job_id=body.job_id,
        company=company,
        title=title,
        job_url=url,
        status=body.status,
        applied_on=body.applied_on,
        next_step=body.next_step,
        next_step_due=body.next_step_due,
        notes=body.notes,
        status_history=[{"status": body.status.value, "at": now}],
    )
    db.add(a)
    db.commit()
    return ApplicationOut.model_validate(a)


@router.patch("/{app_id}", response_model=ApplicationOut)
def update_application(
    app_id: int,
    body: ApplicationPatch,
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    a = _owned(db, profile, app_id)
    data = body.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)
    if new_status is not None and new_status != a.status:
        a.status = new_status
        a.status_history = [*a.status_history, {"status": new_status.value, "at": datetime.now(UTC).isoformat()}]
        if new_status == ApplicationStatus.applied and a.applied_on is None and "applied_on" not in data:
            a.applied_on = datetime.now(UTC).date()
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    return ApplicationOut.model_validate(a)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    app_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> None:
    db.delete(_owned(db, profile, app_id))
    db.commit()
