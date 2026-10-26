"""Health and public configuration (which integrations are real vs. mock), plus learning resources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import LearningResource, User
from app.repositories.career import resource_dict, seed_resources
from app.services.roles import ROLES

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("select 1"))
    return {"status": "ok"}


@router.get("/meta")
def meta() -> dict:
    s = get_settings()
    live_jobs = [p for p in s.job_providers if p != "mock"]
    return {
        "app": s.app_name,
        "environment": s.environment,
        "demo_mode": s.demo_mode,
        "llm": dict(zip(("provider", "model"), s.active_llm(), strict=True)),
        "job_providers": s.job_providers,
        "mock_jobs": "mock" in s.job_providers,
        "live_job_providers": live_jobs,
        "code_sandbox": s.code_sandbox,
        "max_upload_mb": s.max_upload_mb,
        "roles": [{"key": r.key, "title": r.title} for r in ROLES],
    }


@router.get("/resources", response_model=list[dict])
def list_resources(
    skill: str | None = Query(default=None, max_length=80),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.scalar(select(LearningResource.id).limit(1)) is None:
        seed_resources(db)
        db.commit()
    rows = db.scalars(select(LearningResource).order_by(LearningResource.title))
    return [resource_dict(r) for r in rows if skill is None or skill in (r.skills or [])]
