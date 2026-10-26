"""Job data providers.

Only authorized, publicly documented sources are used:
- Greenhouse Job Board API (public, unauthenticated): https://developers.greenhouse.io/job-board.html
- Lever Postings API (public, unauthenticated):       https://github.com/lever/postings-api
- MockJobProvider: bundled, clearly labeled demo listings for local development and tests.

No scraping, no authentication bypass. A listing fetched from a provider means "present in the
company's published feed at retrieved_at" - it is never presented as a guarantee the role is open.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx

from app.core.logging import get_logger, log_ctx
from app.schemas.agents import NormalizedJob
from app.services import job_parsing as jp

logger = get_logger(__name__)
DATA_DIR = Path(__file__).resolve().parent / "data"
USER_AGENT = "CareerPilotAI/0.1 (job research; respects public API terms)"


class JobQuery:
    def __init__(self, keywords: list[str], locations: list[str] | None = None, limit: int = 50) -> None:
        self.keywords = [k.lower() for k in keywords if k]
        self.locations = [loc.lower() for loc in (locations or [])]
        self.limit = limit

    def location_rank(self, location: str) -> int:
        """0 = fits the preferred locations (incl. unrestricted or matching remote), 1 = elsewhere."""
        return 0 if jp.location_fits(location, self.locations) else 1

    def select(self, jobs: list[NormalizedJob]) -> list[NormalizedJob]:
        """Per-source cap: preferred locations first, then most recently posted."""
        epoch = datetime.min.replace(tzinfo=UTC)
        ranked = sorted(jobs, key=lambda j: (self.location_rank(j.location), -(j.posted_at or epoch).timestamp()))
        return ranked[: self.limit]

    def title_matches(self, title: str) -> bool:
        if not self.keywords:
            return True
        t = title.lower()
        # Whole words only: "api" must not match "capital".
        return any(
            all(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", t) for w in kw.split()) for kw in self.keywords
        )


class JobProvider(Protocol):
    name: str

    def search(self, query: JobQuery) -> list[NormalizedJob]: ...


def build_job(
    *,
    source: str,
    external_id: str,
    title: str,
    company: str,
    location: str,
    description: str,
    application_url: str | None,
    source_url: str | None,
    posted_at: datetime | None,
    is_demo: bool,
    arrangement_hint: str | None = None,
    employment_hint: str | None = None,
    salary: tuple[int | None, int | None, str | None] = (None, None, None),
    stale_after_days: int = 30,
) -> NormalizedJob:
    min_years = jp.extract_min_years(description)
    job = NormalizedJob(
        source=source,
        external_id=str(external_id),
        title=title.strip()[:200],
        company=company.strip()[:200],
        location=(location or "").strip()[:200],
        work_arrangement=jp.infer_arrangement(location or "", description, arrangement_hint),
        employment_type=jp.infer_employment_type(f"{employment_hint or ''} {title} {description[:500]}"),
        seniority=jp.infer_seniority(title, min_years),
        min_years_experience=min_years,
        salary_min=salary[0],
        salary_max=salary[1],
        salary_currency=salary[2],
        description=description[:20_000],
        application_url=application_url,
        source_url=source_url,
        posted_at=posted_at,
        retrieved_at=datetime.now(UTC),
        requirements=jp.extract_requirements(description),
        is_demo=is_demo,
    )
    job.quality_flags = jp.quality_flags(job, stale_after_days=stale_after_days)
    return job


def _search_boards(provider: str, boards: list[str], fetch, query: JobQuery) -> list[NormalizedJob]:
    """Fetch boards in parallel. One failing board is logged and skipped; all failing raises."""
    from concurrent.futures import ThreadPoolExecutor

    def one(board: str) -> list[NormalizedJob] | Exception:
        try:
            return query.select(fetch(board))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning(
                "Job board fetch failed", extra=log_ctx(provider=provider, board=board, error=str(exc)[:200])
            )
            return exc

    with ThreadPoolExecutor(max_workers=min(8, len(boards) or 1)) as pool:
        results = list(pool.map(one, boards))
    ok = [r for r in results if not isinstance(r, Exception)]
    if boards and not ok:
        raise next(r for r in results if isinstance(r, Exception))
    return [job for r in ok for job in r]


class MockJobProvider:
    """Bundled demo listings. Company names are fictional; every listing is flagged `demo_data`."""

    name = "mock"

    def __init__(self, stale_after_days: int = 30) -> None:
        self.stale_after_days = stale_after_days

    def search(self, query: JobQuery) -> list[NormalizedJob]:
        raw = json.loads((DATA_DIR / "mock_jobs.json").read_text(encoding="utf-8"))
        now = datetime.now(UTC)
        out = []
        for item in raw:
            if not query.title_matches(item["title"]):
                continue
            description = "\n".join(
                [
                    item["intro"],
                    "",
                    "Responsibilities:",
                    *[f"- {r}" for r in item["responsibilities"]],
                    "",
                    "Requirements:",
                    *[f"- {r}" for r in item["requirements"]],
                    "",
                    "Nice to have:",
                    *[f"- {r}" for r in item["nice_to_have"]],
                ]
            )
            posted = now - timedelta(days=item["posted_days_ago"]) if item.get("posted_days_ago") is not None else None
            out.append(
                build_job(
                    source=self.name,
                    external_id=item["id"],
                    title=item["title"],
                    company=item["company"],
                    location=item["location"],
                    description=description,
                    application_url=None,
                    source_url=None,
                    posted_at=posted,
                    is_demo=True,
                    arrangement_hint=item.get("arrangement"),
                    employment_hint=item.get("employment_type"),
                    salary=(item.get("salary_min"), item.get("salary_max"), item.get("salary_currency")),
                    stale_after_days=self.stale_after_days,
                )
            )
        return out[: query.limit]


class GreenhouseProvider:
    name = "greenhouse"
    BASE = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(
        self, boards: list[str], timeout: float = 15.0, stale_after_days: int = 30, client: httpx.Client | None = None
    ) -> None:
        self.boards = boards
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.stale_after_days = stale_after_days

    def search(self, query: JobQuery) -> list[NormalizedJob]:
        return _search_boards(self.name, self.boards, lambda b: self._board(b, query), query)

    def _board(self, board: str, query: JobQuery) -> list[NormalizedJob]:
        out: list[NormalizedJob] = []
        meta = self.client.get(f"{self.BASE}/{board}")
        company = meta.json().get("name", board) if meta.status_code == 200 else board
        company = re.sub(r"\s+(job board|careers?|jobs)$", "", company, flags=re.I).strip() or board
        resp = self.client.get(f"{self.BASE}/{board}/jobs", params={"content": "true"})
        resp.raise_for_status()
        for j in resp.json().get("jobs", []):
            if not query.title_matches(j.get("title", "")):
                continue
            posted = _parse_iso(j.get("first_published") or j.get("updated_at"))
            out.append(
                build_job(
                    source=self.name,
                    external_id=f"{board}:{j['id']}",
                    title=j.get("title", ""),
                    company=company,
                    location=(j.get("location") or {}).get("name", ""),
                    description=jp.strip_html(j.get("content", "")),
                    application_url=j.get("absolute_url"),
                    source_url=f"{self.BASE}/{board}/jobs/{j['id']}",
                    posted_at=posted,
                    is_demo=False,
                    stale_after_days=self.stale_after_days,
                )
            )
        return out


class LeverProvider:
    name = "lever"
    BASE = "https://api.lever.co/v0/postings"

    def __init__(
        self,
        companies: list[str],
        timeout: float = 15.0,
        stale_after_days: int = 30,
        client: httpx.Client | None = None,
    ) -> None:
        self.companies = companies
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.stale_after_days = stale_after_days

    def search(self, query: JobQuery) -> list[NormalizedJob]:
        return _search_boards(self.name, self.companies, lambda c: self._company(c, query), query)

    def _company(self, company: str, query: JobQuery) -> list[NormalizedJob]:
        out: list[NormalizedJob] = []
        resp = self.client.get(f"{self.BASE}/{company}", params={"mode": "json"})
        resp.raise_for_status()
        for p in resp.json():
            title = p.get("text", "")
            if not query.title_matches(title):
                continue
            parts = [p.get("descriptionPlain") or jp.strip_html(p.get("description", ""))]
            for lst in p.get("lists", []):
                parts.append(f"\n{lst.get('text', '')}:")
                parts.append(jp.strip_html(lst.get("content", "")))
            parts.append(p.get("additionalPlain") or "")
            cats = p.get("categories") or {}
            created = p.get("createdAt")
            out.append(
                build_job(
                    source=self.name,
                    external_id=f"{company}:{p['id']}",
                    title=title,
                    company=company.title(),
                    location=cats.get("location", ""),
                    description="\n".join(x for x in parts if x),
                    application_url=p.get("applyUrl") or p.get("hostedUrl"),
                    source_url=p.get("hostedUrl"),
                    posted_at=datetime.fromtimestamp(created / 1000, UTC) if created else None,
                    is_demo=False,
                    arrangement_hint=p.get("workplaceType"),
                    employment_hint=cats.get("commitment"),
                    stale_after_days=self.stale_after_days,
                )
            )
        return out


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        logger.info("Unparseable job date", extra=log_ctx(value=value))
        return None


def build_providers(
    names: list[str], *, greenhouse_boards: list[str], lever_companies: list[str], timeout: float, stale_after_days: int
) -> list[JobProvider]:
    providers: list[JobProvider] = []
    for name in names:
        if name == "mock":
            providers.append(MockJobProvider(stale_after_days))
        elif name == "greenhouse" and greenhouse_boards:
            providers.append(GreenhouseProvider(greenhouse_boards, timeout, stale_after_days))
        elif name == "lever" and lever_companies:
            providers.append(LeverProvider(lever_companies, timeout, stale_after_days))
        else:
            logger.warning("Job provider skipped (unknown or not configured)", extra=log_ctx(provider=name))
    return providers
