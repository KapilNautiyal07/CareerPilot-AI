"""Reproducible agent evaluation.

    python -m app.evaluation.run_eval                 # isolated SQLite DB, mock providers
    python -m app.evaluation.run_eval --database-url postgresql+psycopg://...  # e.g. a scratch Postgres DB
    LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... python -m app.evaluation.run_eval   # measure a real model

Measures skill-extraction accuracy, unsupported-claim and hallucination-survival rates, job
ranking quality (NDCG@5, P@3), roadmap relevance, retrieval quality (catalog coverage of the skill
taxonomy, share of learn/practice tasks with a resource, level fit of attached resources),
soft-skill task shape, interview grading consistency, source URL validity, workflow completion,
per-node latency and LLM usage. Writes a JSON report and prints
a Markdown summary.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = HERE / "data" / "eval_set.json"
SAMPLE = HERE.parent / "demo" / "sample_resume.txt"


def _configure(database_url: str | None) -> str:
    url = database_url or f"sqlite:///{(Path(tempfile.mkdtemp(prefix='cp-eval-')) / 'eval.db').as_posix()}"
    os.environ.update(
        {
            "DATABASE_URL": url,
            "CHECKPOINTER": "memory",
            "WORKFLOW_INLINE": "true",
            "JOB_PROVIDERS": "mock",
            "ENVIRONMENT": "test",
            "STORAGE_DIR": tempfile.mkdtemp(prefix="cp-eval-files-"),
        }
    )
    os.environ.setdefault("LLM_PROVIDER", "mock")
    return url


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def ndcg(ranked_ids: list[str], labels: dict[str, int], k: int = 5) -> float:
    def dcg(rels: list[int]) -> float:
        return sum((2**r - 1) / math.log2(i + 2) for i, r in enumerate(rels[:k]))

    actual = dcg([labels.get(j, 0) for j in ranked_ids])
    ideal = dcg(sorted(labels.values(), reverse=True))
    return actual / ideal if ideal else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--output", default=str(HERE.parents[1] / "var" / "eval_report.json"))
    args = parser.parse_args()
    url = _configure(args.database_url)

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.agents.interview_agent import InterviewCoachAgent, bank_item
    from app.agents.resume_agent import ResumeAnalysisAgent
    from app.core.logging import configure_logging
    from app.db.base import Base
    from app.db.session import configure_engine, session_scope
    from app.graph.runner import runner
    from app.models import JobListing, Resume, User, WorkflowExecution
    from app.models.enums import SkillSource, WorkflowKind
    from app.repositories import career as repo
    from app.schemas.agents import LLMResumeExtraction
    from app.services import skills as tax
    from app.services.llm import MockLLM, get_llm
    from app.services.matching import CandidateSkill
    from app.tools.job_providers import JobQuery, MockJobProvider

    configure_logging("ERROR")
    engine = configure_engine(url)
    if url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    llm = get_llm()
    report: dict = {
        "dataset_version": data["version"],
        "llm_provider": llm.name,
        "database": url.split(":")[0],
        "candidates": {},
    }

    from app.services.job_parsing import dedupe

    all_jobs = dedupe(MockJobProvider().search(JobQuery([], limit=100)))
    with session_scope() as db:
        stored = repo.upsert_jobs(db, all_jobs)
        repo.seed_resources(db)
    id_by_external = {}
    with session_scope() as db:
        for row in db.query(JobListing).all():
            id_by_external[row.id] = row.external_id

    extraction, unsupported_rates, ndcgs, p3s, coverage, traced, completions, latencies = [], [], [], [], [], [], [], {}
    with_res: list[float] = []
    level_fit: list[bool] = []
    soft_projects = 0
    for cand in data["candidates"]:
        text = SAMPLE.read_text(encoding="utf-8") if cand.get("resume_file") == "sample" else cand["resume_text"]
        started = time.monotonic()
        analysis = ResumeAnalysisAgent(llm).analyze(text)
        extract_ms = int((time.monotonic() - started) * 1000)
        explicit = {s.skill for s in analysis.skills if s.source == SkillSource.explicit}
        expected = set(cand["expected_skills"])
        tp = len(explicit & expected)
        precision = tp / len(explicit) if explicit else 0.0
        recall = tp / len(expected) if expected else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        extraction.append((precision, recall, f1))
        src = _norm(text)
        unsupported = [
            s.skill
            for s in analysis.skills
            if s.source == SkillSource.explicit and not any(_norm(e)[:60] in src for e in s.evidence)
        ]
        unsupported_rates.append(len(unsupported) / max(1, len(explicit)))

        have = {s.skill: CandidateSkill(s.skill, s.evidence_level, None, s.evidence) for s in analysis.skills}
        from app.agents.job_research_agent import JobResearchAgent
        from app.services.matching import Preferences

        ranked = JobResearchAgent.rank(
            stored, have, analysis.total_years_experience, Preferences(), 30, cand.get("target_role")
        )
        ranked_ext = [id_by_external[m.job_id] for m in ranked]
        n5 = ndcg(ranked_ext, cand["job_relevance"])
        p3 = sum(cand["job_relevance"].get(j, 0) >= 1 for j in ranked_ext[:3]) / 3
        ndcgs.append(n5)
        p3s.append(p3)

        # Full orchestrated workflow for this candidate.
        with session_scope() as db:
            user = User(email=f"eval-{cand['id']}@example.com", password_hash="!unusable", full_name=cand["id"])  # noqa: S106
            db.add(user)
            db.flush()
            profile = repo.get_or_create_profile(db, user.id)
            profile.target_role = cand["target_role"]
            resume = Resume(
                profile_id=profile.id,
                original_filename="eval.txt",
                content_type="text/plain",
                storage_key=f"eval-{cand['id']}",
                size_bytes=len(text),
                sha256=cand["id"],
                raw_text=text,
            )
            db.add(resume)
            db.flush()
            profile.active_resume_id = resume.id
            uid, pid = user.id, profile.id
        ex_id = runner.start(WorkflowKind.career_analysis, uid, pid, {})
        with session_scope() as db:
            ex = db.get(WorkflowExecution, ex_id)
            status, result, steps = ex.status.value, ex.result or {}, ex.steps or []
        completions.append(status in ("succeeded", "partial"))
        for s in steps:
            latencies.setdefault(s["node"], []).append(s.get("duration_ms", 0))
        issues = {i["code"] for i in result.get("issues", [])}
        with session_scope() as db:
            from app.models import SkillGap

            gaps = (
                db.query(SkillGap)
                .filter(SkillGap.profile_id == pid, SkillGap.is_current.is_(True))
                .order_by(SkillGap.priority_score.desc())
                .all()
            )
            rm = repo.active_roadmap(db, pid)
            covered = set(rm.summary.get("covered_skills", [])) if rm else set()
            # Retrieval quality as the user sees it: resources attached to roadmap tasks.
            status_of = {g.skill: g.status.value for g in gaps}
            study = [t for t in (rm.tasks if rm else []) if t.task_type.value in ("learn", "practice") and t.skill]
            with_res.append(sum(bool(t.resources) for t in study) / len(study) if study else 1.0)
            fits = []
            for t in study:
                levels = {r.level for r in t.resources}
                if not levels or t.task_type.value != "learn":
                    continue
                st = status_of.get(t.skill)
                # Missing skills should not get only advanced material; partial ones not only beginner.
                fits.append(
                    not ((st == "missing" and levels == {"advanced"}) or (st == "partial" and levels == {"beginner"}))
                )
            level_fit.extend(fits)
            soft_projects += sum(
                t.task_type.value == "project" and tax.category_of(t.skill or "") == "soft"
                for t in (rm.tasks if rm else [])
            )
        top_required = [
            g.skill for g in gaps if g.status.value != "matched" and g.requirement_level.value == "required"
        ][:5]
        coverage.append(sum(s in covered for s in top_required) / len(top_required) if top_required else 1.0)
        traced.append(0.0 if "task_without_gap" in issues else 1.0)
        report["candidates"][cand["id"]] = {
            "extraction": {
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "missed": sorted(expected - explicit),
                "extra": sorted(explicit - expected),
                "latency_ms": extract_ms,
            },
            "unsupported_skills": unsupported,
            "ranking": {"ndcg@5": round(n5, 3), "p@3": round(p3, 3), "top3": ranked_ext[:3]},
            "workflow": {
                "status": status,
                "revisions": result.get("revisions"),
                "readiness": result.get("readiness"),
                "evaluation_valid": result.get("evaluation_valid"),
                "llm_usage": result.get("llm_usage"),
            },
            "roadmap_top_gap_coverage": round(coverage[-1], 3),
            "top_required_gaps": top_required,
        }

    # Hallucination survival: a scripted LLM invents skills/achievements; grounding must drop them.
    fake = LLMResumeExtraction(
        skills=[
            LLMResumeExtraction.Skill(name="Kubernetes", quote="Managed Kubernetes clusters at scale"),
            LLMResumeExtraction.Skill(name="Rust", quote="Wrote high-performance Rust services"),
            LLMResumeExtraction.Skill(name="Terraform", quote="Python, Flask, FastAPI"),
        ],
        experiences=[],
        achievements=["Led a team of 40 engineers"],
        summary="Staff engineer.",
    )
    adv = ResumeAnalysisAgent(MockLLM({"resume_extraction": fake})).analyze(SAMPLE.read_text(encoding="utf-8"))
    survived = [s for s in ("Kubernetes", "Rust", "Terraform") if s in {x.skill for x in adv.skills}]
    survived += [a for a in adv.achievements if "40 engineers" in a]

    # Interview grading consistency and ordering.
    coach = InterviewCoachAgent(llm)
    max_spread, ordered, total_pairs = 0.0, 0, 0
    interview_rows = {}
    for case in data["interview_cases"]:
        q = bank_item(case["question"])
        scores = {}
        for label, answer in case["answers"].items():
            runs = [coach.evaluate(q, answer).score for _ in range(3)]
            max_spread = max(max_spread, max(runs) - min(runs))
            scores[label] = statistics.mean(runs)
        for hi, lo in (("good", "medium"), ("medium", "poor"), ("good", "poor")):
            total_pairs += 1
            ordered += scores[hi] > scores[lo]
        interview_rows[case["question"]] = {k: round(v, 1) for k, v in scores.items()}

    with session_scope() as db:
        from app.models import LearningResource

        resources = db.query(LearningResource).all()
        tagged = {s for r in resources for s in (r.skills or [])}
        taxonomy_coverage = sum(d.name in tagged for d in tax.TAXONOMY) / len(tax.TAXONOMY)
        valid_urls = sum(bool(re.match(r"^https://[\w.-]+\.[a-z]{2,}(/\S*)?$", r.url)) for r in resources)
        verification = {}
        for r in resources:
            verification[r.verification_status.value] = verification.get(r.verification_status.value, 0) + 1

    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    report["summary"] = {
        "skill_extraction_precision": mean([p for p, _, _ in extraction]),
        "skill_extraction_recall": mean([r for _, r, _ in extraction]),
        "skill_extraction_f1": mean([f for _, _, f in extraction]),
        "unsupported_claim_rate": mean(unsupported_rates),
        "hallucination_survival_rate": round(len(survived) / 4, 3),
        "job_ranking_ndcg@5": mean(ndcgs),
        "job_ranking_p@3": mean(p3s),
        "roadmap_top_gap_coverage": mean(coverage),
        "roadmap_tasks_traceable": mean(traced),
        "resource_taxonomy_coverage": round(taxonomy_coverage, 3),
        "study_tasks_with_resources": mean(with_res),
        "resource_level_fit": mean([float(f) for f in level_fit]),
        "soft_skill_project_tasks": soft_projects,
        "interview_score_max_spread_across_runs": round(max_spread, 2),
        "interview_ordering_accuracy": round(ordered / total_pairs, 3),
        "resource_url_wellformed_rate": round(valid_urls / len(resources), 3) if resources else None,
        "resource_verification_status": verification,
        "workflow_completion_rate": mean([1.0 if c else 0.0 for c in completions]),
        "node_latency_ms_p50": {k: int(statistics.median(v)) for k, v in latencies.items()},
    }
    report["interview_cases"] = interview_rows
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"# CareerPilot agent evaluation (LLM provider: {llm.name}, DB: {report['database']})\n")
    print("| Metric | Value |\n|---|---|")
    for k, v in report["summary"].items():
        print(f"| {k} | {v} |")
    print(
        "\n| Candidate | P | R | F1 | NDCG@5 | P@3 | Top-gap coverage | Workflow |\n|---|---|---|---|---|---|---|---|"
    )
    for cid, c in report["candidates"].items():
        e, rk = c["extraction"], c["ranking"]
        print(
            f"| {cid} | {e['precision']} | {e['recall']} | {e['f1']} | {rk['ndcg@5']} | {rk['p@3']} | "
            f"{c['roadmap_top_gap_coverage']} | {c['workflow']['status']} |"
        )
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
