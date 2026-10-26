"""Job Research Agent: discover, normalize, deduplicate, flag and rank job listings."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import httpx

from app.core.logging import get_logger, log_ctx
from app.schemas.agents import JobMatchScore, NormalizedJob
from app.services import job_parsing as jp
from app.services.matching import CandidateSkill, Preferences, match_job
from app.services.roles import resolve_role
from app.tools.job_providers import JobProvider, JobQuery

logger = get_logger(__name__)


@dataclass
class Discovery:
    jobs: list[NormalizedJob]
    providers_used: list[str]
    errors: list[dict] = field(default_factory=list)
    fetched: int = 0


class JobResearchAgent:
    name = "job_research"

    def __init__(self, providers: list[JobProvider], max_workers: int = 4) -> None:
        self.providers = providers
        self.max_workers = max_workers

    @staticmethod
    def keywords_for(target_role: str) -> list[str]:
        role = resolve_role(target_role)
        kws = {target_role.lower().strip()} if target_role else set()
        if role:
            kws.update(role.aliases)
            kws.add(role.title.lower())
            # Generic titles often used for the same role.
            if role.key in {"backend_engineer", "python_developer"}:
                kws.update({"backend", "python developer", "software engineer - backend", "api"})
        return sorted(k for k in kws if k)

    def discover(self, target_role: str, locations: list[str] | None = None, limit: int = 25) -> Discovery:
        """`limit` applies per company board, preferring the candidate's locations."""
        query = JobQuery(self.keywords_for(target_role), locations, limit)
        fetched: list[NormalizedJob] = []
        errors: list[dict] = []
        used: list[str] = []
        # Providers are independent; query them in parallel with per-provider failure isolation.
        with ThreadPoolExecutor(max_workers=max(1, min(self.max_workers, len(self.providers) or 1))) as pool:
            futures = {pool.submit(p.search, query): p.name for p in self.providers}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    jobs = fut.result()
                    fetched.extend(jobs)
                    used.append(name)
                except httpx.HTTPError as exc:
                    errors.append({"provider": name, "error": exc.__class__.__name__, "message": str(exc)[:200]})
                    logger.warning("Job provider failed", extra=log_ctx(provider=name, error=exc.__class__.__name__))
                except (ValueError, KeyError) as exc:
                    errors.append({"provider": name, "error": "bad_response", "message": str(exc)[:200]})
                    logger.warning("Job provider returned unexpected data", extra=log_ctx(provider=name))
        unique = jp.dedupe(fetched)
        return Discovery(jobs=unique, providers_used=sorted(used), errors=errors, fetched=len(fetched))

    @staticmethod
    def rank(
        jobs: list[tuple[int, NormalizedJob]],
        have: dict[str, CandidateSkill],
        years: float | None,
        prefs: Preferences,
        stale_after_days: int,
        target_role: str | None = None,
    ) -> list[JobMatchScore]:
        scores = [match_job(job_id, job, have, years, prefs, stale_after_days, target_role) for job_id, job in jobs]
        # Suspicious listings are ranked but never boosted; ties broken by skill fit.
        return sorted(scores, key=lambda s: (s.score, s.skill_score), reverse=True)
