"""Regression guard over the agent-evaluation dataset (full harness: python -m app.evaluation.run_eval)."""

import json

import pytest

from app.agents.job_research_agent import JobResearchAgent
from app.agents.resume_agent import ResumeAnalysisAgent
from app.evaluation.run_eval import DATASET, SAMPLE, ndcg
from app.models.enums import SkillSource
from app.services.job_parsing import dedupe
from app.services.llm import MockLLM
from app.services.matching import CandidateSkill, Preferences
from app.tools.job_providers import JobQuery, MockJobProvider

DATA = json.loads(DATASET.read_text(encoding="utf-8"))
JOBS = dedupe(MockJobProvider().search(JobQuery([], limit=100)))


@pytest.mark.parametrize("cand", DATA["candidates"], ids=lambda c: c["id"])
def test_extraction_and_ranking_thresholds(cand):
    text = SAMPLE.read_text(encoding="utf-8") if cand.get("resume_file") == "sample" else cand["resume_text"]
    analysis = ResumeAnalysisAgent(MockLLM()).analyze(text)
    explicit = {s.skill for s in analysis.skills if s.source == SkillSource.explicit}
    expected = set(cand["expected_skills"])
    tp = len(explicit & expected)
    assert tp / len(explicit) >= 0.9 and tp / len(expected) >= 0.9, explicit ^ expected
    have = {s.skill: CandidateSkill(s.skill, s.evidence_level, None, s.evidence) for s in analysis.skills}
    ranked = JobResearchAgent.rank(
        list(enumerate(JOBS)), have, analysis.total_years_experience, Preferences(), 30, cand.get("target_role")
    )
    order = [JOBS[m.job_id].external_id for m in ranked]
    assert ndcg(order, cand["job_relevance"]) >= 0.7


def test_ndcg_metric():
    assert ndcg(["a", "b"], {"a": 2, "b": 1}) == pytest.approx(1.0)
    assert ndcg(["b", "a"], {"a": 2, "b": 1}) < 1.0
