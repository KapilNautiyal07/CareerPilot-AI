"""Configurable data retention (RESUME_RETENTION_DAYS, WORKFLOW_RETENTION_DAYS; 0 disables)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import CandidateProfile, Resume, WorkflowExecution
from app.models.enums import WorkflowStatus
from app.services.storage import get_storage

logger = get_logger(__name__)


def purge_expired(now: datetime | None = None) -> dict[str, int]:
    s = get_settings()
    now = now or datetime.now(UTC)
    purged = {"resumes_purged": 0, "workflows_purged": 0}
    with session_scope() as db:
        if s.resume_retention_days > 0:
            cutoff = now - timedelta(days=s.resume_retention_days)
            old = list(db.scalars(select(Resume).where(Resume.uploaded_at < cutoff)))
            storage = get_storage()
            for r in old:
                for p in db.scalars(select(CandidateProfile).where(CandidateProfile.active_resume_id == r.id)):
                    p.active_resume_id = None
                storage.delete(r.storage_key)
                db.delete(r)
            purged["resumes_purged"] = len(old)
        if s.workflow_retention_days > 0:
            cutoff = now - timedelta(days=s.workflow_retention_days)
            res = db.execute(
                delete(WorkflowExecution).where(
                    WorkflowExecution.created_at < cutoff,
                    WorkflowExecution.status.in_(
                        [WorkflowStatus.succeeded, WorkflowStatus.partial, WorkflowStatus.failed]
                    ),
                )
            )
            purged["workflows_purged"] = res.rowcount or 0
    return purged
