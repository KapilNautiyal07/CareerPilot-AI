"""Operational commands.

python -m app.cli seed-resources      # load/refresh the curated learning-resource catalog
python -m app.cli verify-resources    # HTTP-check every resource URL and record the result
python -m app.cli purge               # apply retention policies now
python -m app.cli seed-demo           # create demo@example.com with a sample resume + analysis
python -m app.cli remove-demo-jobs    # delete bundled demo listings after switching to real feeds
python -m app.cli llm-check           # one small request to verify the configured LLM key/model
python -m app.cli graph career|progress|interview   # print the Mermaid diagram of a graph
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import session_scope
from app.models import LearningResource, Resume, User
from app.models.enums import ResourceVerification, ResumeStatus

DEMO_EMAIL = "demo@example.com"  # .local is rejected by email validation (special-use TLD)
LEGACY_DEMO_EMAIL = "demo@careerpilot.local"
DEMO_PASSWORD = "demo-password-123"  # noqa: S105 - documented local demo account


def seed_resources() -> None:
    from app.repositories.career import seed_resources as seed

    with session_scope() as db:
        print(f"added {seed(db)} resources")


def verify_resources(timeout: float = 10.0) -> None:
    """Records only what an actual request observed; never marks a link verified without checking.

    reachable   -> 2xx/3xx response
    unreachable -> definitive failure (404, 410, other 4xx, DNS/connection refused)
    unverified  -> could not tell (401/403/429 bot protection, timeouts, 5xx); retried on the next run
    """
    inconclusive = {401, 403, 429}
    headers = {"User-Agent": "CareerPilotAI link checker"}
    with session_scope() as db, httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for r in db.scalars(select(LearningResource)):
            try:
                resp = client.head(r.url)
                if resp.status_code in (403, 405) or resp.status_code >= 500:
                    resp = client.get(r.url)
                r.http_status = resp.status_code
                if resp.status_code < 400:
                    r.verification_status = ResourceVerification.reachable
                elif resp.status_code in inconclusive or resp.status_code >= 500:
                    r.verification_status = ResourceVerification.unverified
                else:
                    r.verification_status = ResourceVerification.unreachable
            except httpx.TimeoutException:
                r.http_status = None
                r.verification_status = ResourceVerification.unverified
            except httpx.HTTPError as exc:
                r.http_status = None
                r.verification_status = ResourceVerification.unreachable
                print(f"  error {r.url}: {exc.__class__.__name__}")
            r.last_checked_at = datetime.now(UTC)
            print(f"{r.verification_status.value:12} {r.http_status} {r.url}")


def purge() -> None:
    from app.services.retention import purge_expired

    print(json.dumps(purge_expired()))


def seed_demo() -> None:
    from app.agents.resume_agent import ResumeAnalysisAgent
    from app.core.security import hash_password
    from app.repositories import career as repo
    from app.services.llm import get_llm
    from app.services.storage import get_storage

    text = (Path(__file__).parent / "demo" / "sample_resume.txt").read_text(encoding="utf-8")
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email.in_([DEMO_EMAIL, LEGACY_DEMO_EMAIL])))
        if user is not None:
            user.email = DEMO_EMAIL
        if user is None:
            user = User(
                email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), full_name="Demo Candidate", is_demo=True
            )
            db.add(user)
            db.flush()
        profile = repo.get_or_create_profile(db, user.id)
        profile.target_role = profile.target_role or "Backend Engineer"
        profile.onboarding_completed = True
        pref = repo.get_or_create_preference(db, user.id)
        pref.weekly_study_hours, pref.study_days_per_week = 8, 5
        pref.target_deadline = date.today() + timedelta(weeks=10)
        pref.preferred_locations, pref.work_arrangements = ["Bengaluru"], ["hybrid", "remote"]
        digest = hashlib.sha256(text.encode()).hexdigest()
        resume = db.scalar(select(Resume).where(Resume.profile_id == profile.id, Resume.sha256 == digest))
        if resume is None:
            resume = Resume(
                profile_id=profile.id,
                original_filename="DEMO sample resume (fictional).txt",
                content_type="text/plain",
                storage_key=get_storage().save(text.encode(), ".txt"),
                size_bytes=len(text),
                sha256=digest,
                raw_text=text,
                status=ResumeStatus.parsed,
            )
            db.add(resume)
            db.flush()
        repo.apply_resume_analysis(db, profile, resume, ResumeAnalysisAgent(get_llm()).analyze(text))
        repo.seed_resources(db)
    print(f"Demo user ready: {DEMO_EMAIL} / {DEMO_PASSWORD} (run the career analysis from the dashboard)")


def graph(kind: str) -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from app.graph.career_graph import build_career_graph
    from app.graph.interview_graph import build_interview_graph
    from app.graph.progress_graph import build_progress_graph

    builders = {"career": build_career_graph, "progress": build_progress_graph, "interview": build_interview_graph}
    print(builders[kind](InMemorySaver()).get_graph().draw_mermaid())


def remove_demo_jobs() -> None:
    """Delete bundled demo listings (and their matches/requirements via cascade) after switching to real feeds."""
    from app.models import JobListing

    with session_scope() as db:
        demo = list(db.scalars(select(JobListing).where(JobListing.is_demo.is_(True))))
        for job in demo:
            db.delete(job)
    print(f"removed {len(demo)} demo job listings (tracked applications are kept)")


def llm_check() -> None:
    """One small structured call to confirm the configured LLM provider, key and model work."""
    from app.core.config import get_settings
    from app.schemas.agents import LLMRubricJudgement
    from app.services.llm import get_llm

    provider, model = get_settings().active_llm()
    if provider == "mock":
        print("LLM provider: mock (set LLM_PROVIDER and the matching API key in .env to use a real model)")
        return
    result = get_llm().structured(
        task="llm_check",
        system="Grade the answer against one criterion: 'mentions an idempotency key'. Quote the answer verbatim.",
        content="Answer: We send an idempotency key with every payment request.",
        schema=LLMRubricJudgement,
        max_tokens=500,
    )
    if result.value is None:
        print(f"FAILED: {provider} / {model}: {result.error} ({result.latency_ms} ms)")
        raise SystemExit(1)
    c = result.value.criteria[0] if result.value.criteria else None
    print(
        f"OK: {provider} / {model} answered in {result.latency_ms} ms "
        f"({result.input_tokens} in / {result.output_tokens} out tokens)"
    )
    if c:
        print(f"  criterion={c.id} met={c.met} quote={c.quote!r}")


def main() -> None:
    configure_logging("WARNING")
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("seed-resources", "verify-resources", "purge", "seed-demo", "llm-check", "remove-demo-jobs"):
        sub.add_parser(name)
    g = sub.add_parser("graph")
    g.add_argument("kind", choices=["career", "progress", "interview"])
    args = parser.parse_args()
    {
        "seed-resources": seed_resources,
        "verify-resources": verify_resources,
        "purge": purge,
        "seed-demo": seed_demo,
        "llm-check": llm_check,
        "remove-demo-jobs": remove_demo_jobs,
        "graph": lambda: graph(args.kind),
    }[args.cmd]()


if __name__ == "__main__":
    main()
