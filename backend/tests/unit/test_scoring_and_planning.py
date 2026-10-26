from datetime import date, timedelta

import pytest

from app.agents.evaluation_agent import CareerEvaluationAgent
from app.agents.roadmap_agent import PlanParams, RoadmapAgent, replan_params
from app.agents.skill_gap_agent import SkillGapAgent
from app.models.enums import EvidenceLevel, GapStatus, RequirementLevel, TaskType
from app.schemas.agents import NormalizedJob, SkillRequirement
from app.services.matching import CandidateSkill, Preferences, match_job, score_skills, skill_credit
from app.services.roles import BY_KEY, resolve_role

R, P = RequirementLevel.required, RequirementLevel.preferred


def have(**levels) -> dict[str, CandidateSkill]:
    out = {}
    for name, level in levels.items():
        skill = name.replace("_", " ")
        out[skill] = CandidateSkill(
            skill,
            level if isinstance(level, EvidenceLevel) else EvidenceLevel.assessed,
            None if isinstance(level, EvidenceLevel) else level,
        )
    return out


def test_credit_by_evidence_level():
    h = {
        "Python": CandidateSkill("Python", EvidenceLevel.demonstrated),
        "Redis": CandidateSkill("Redis", EvidenceLevel.listed),
        "Flask": CandidateSkill("Flask", EvidenceLevel.demonstrated),
        "Go": CandidateSkill("Go", EvidenceLevel.demonstrated, assessed_score=30),
    }
    assert skill_credit("Python", h)[:2] == (0.9, GapStatus.matched)
    assert skill_credit("Redis", h)[1] == GapStatus.partial  # a listed keyword is not proficiency
    c, status, _, via = skill_credit("FastAPI", h)
    assert status == GapStatus.partial and via == "Flask" and c == pytest.approx(0.45)
    assert skill_credit("Go", h)[0] == pytest.approx(0.3)  # a weak assessment lowers credit
    assert skill_credit("Kubernetes", h)[:2] == (0.0, GapStatus.missing)


def test_skill_score_weights_required_over_preferred():
    h = {"Python": CandidateSkill("Python", EvidenceLevel.demonstrated)}
    only_req, _ = score_skills([SkillRequirement(skill="Python", level=R), SkillRequirement(skill="Kafka", level=P)], h)
    only_pref, _ = score_skills(
        [SkillRequirement(skill="Python", level=P), SkillRequirement(skill="Kafka", level=R)], h
    )
    assert only_req > only_pref
    assert 0 <= only_pref <= only_req <= 100


def test_match_job_preferences_and_bounds():
    from datetime import UTC, datetime

    job = NormalizedJob(
        source="mock",
        external_id="1",
        title="Backend Engineer",
        company="X",
        location="Pune",
        work_arrangement="onsite",
        retrieved_at=datetime.now(UTC),
        min_years_experience=5,
        requirements=[SkillRequirement(skill="Python", level=R)],
    )
    m = match_job(
        1,
        job,
        {"Python": CandidateSkill("Python", EvidenceLevel.demonstrated)},
        2.5,
        Preferences(locations=["Bengaluru"], arrangements=["remote"]),
        30,
    )
    assert m.experience_score == 50.0 and m.preference_score == 25.0
    assert any("onsite" in n for n in m.notes) and 0 <= m.score <= 100


def _gaps(weeks: float | None = 10, h=None, jobs=None):
    role = BY_KEY["backend_engineer"]
    return SkillGapAgent().analyze(
        target_role="Backend Engineer",
        role=role,
        job_requirements=jobs or [],
        have=h if h is not None else {"Python": CandidateSkill("Python", EvidenceLevel.demonstrated)},
        weeks_until_deadline=weeks,
        weekly_hours=8,
    )


def test_gap_priority_is_reproducible_from_components():
    report = _gaps()
    assert report.demand_source == "role_template"
    for g in report.items:
        c = g.components
        assert g.priority_score == pytest.approx(
            min(100, 100 * c.importance * c.severity * c.urgency * c.effort_penalty), abs=0.11
        )
    assert next(g for g in report.items if g.skill == "Python").status == GapStatus.matched
    assert report.recommended_next and "Python" not in report.recommended_next


def test_tight_deadline_boosts_required_and_demotes_preferred():
    loose, tight = _gaps(weeks=12), _gaps(weeks=2)
    pick = lambda r, s: next(g for g in r.items if g.skill == s)  # noqa: E731
    assert pick(tight, "Docker").components.urgency > pick(loose, "Docker").components.urgency
    assert pick(tight, "Redis").components.urgency < pick(loose, "Redis").components.urgency


def test_job_demand_blends_with_role_baseline():
    jobs = [[SkillRequirement(skill="Kafka", level=R), SkillRequirement(skill="Python", level=R)]] * 4
    report = _gaps(jobs=jobs)
    kafka = next(g for g in report.items if g.skill == "Kafka")
    assert report.demand_source == "jobs+role_template"
    assert kafka.demand_share == pytest.approx(0.7)  # 0.7 * 1.0 job share + 0.3 * 0 baseline


def _plan(weeks=10, weekly=8, report=None, **kw):
    report = report or _gaps(weeks)
    start = date(2026, 11, 2)
    params = PlanParams(
        start=start,
        end=start + timedelta(weeks=weeks) - timedelta(days=1),
        weekly_hours=weekly,
        study_days=5,
        target_role="Backend Engineer",
        tracks=BY_KEY["backend_engineer"].interview_tracks,
        **kw,
    )
    return report, RoadmapAgent().plan(report.items, params, {"Docker": [{"id": 7, "title": "Docker docs"}]})


@pytest.mark.parametrize("weeks,weekly", [(1, 2), (4, 5), (10, 8), (20, 15)])
def test_plan_respects_capacity_and_deadline(weeks, weekly):
    _, plan = _plan(weeks, weekly)
    assert plan.planned_hours <= plan.capacity_hours + 0.01
    for w in plan.weeks:
        assert w.planned_hours <= weekly + 0.01
        for t in w.tasks:
            assert plan.start_date <= t.scheduled_date <= t.due_date <= plan.end_date


def test_plan_caps_single_skill_share_and_attaches_catalog_resources():
    report, plan = _plan(10, 8)
    budget = plan.capacity_hours
    per_skill: dict[str, float] = {}
    for w in plan.weeks:
        for t in w.tasks:
            if t.skill and t.task_type != TaskType.mock_interview:
                per_skill[t.skill] = per_skill.get(t.skill, 0) + t.hours
    dsa = per_skill.get("Data Structures & Algorithms", 0)
    assert 0 < dsa <= 0.36 * budget
    assert any(7 in t.resource_ids for w in plan.weeks for t in w.tasks if t.skill == "Docker")
    assert any(t.task_type == TaskType.mock_interview for w in plan.weeks for t in w.tasks)


def test_listed_only_skill_gets_demonstration_project():
    h = {
        "Python": CandidateSkill("Python", EvidenceLevel.demonstrated),
        "Docker": CandidateSkill("Docker", EvidenceLevel.listed),
    }
    _, plan = _plan(10, 8, report=_gaps(h=h))
    docker = [t for w in plan.weeks for t in w.tasks if t.skill == "Docker"]
    assert any(t.task_type == TaskType.project and "Demonstrate" in t.title for t in docker)
    assert not any(t.task_type == TaskType.learn for t in docker)


def test_replan_params_mastery_and_remediation():
    base = PlanParams(start=date(2026, 1, 1), end=date(2026, 3, 1), weekly_hours=8, study_days=5, target_role="x")
    p = replan_params(
        base,
        tasks=[
            {"status": "completed", "skill": "Docker", "estimated_hours": 2.0},
            {"status": "pending", "skill": "Docker", "estimated_hours": 2.0},
        ],
        assessments={"Redis": 90.0, "System Design": 30.0},
        today=date(2026, 2, 1),
    )
    assert p.completed_hours == {"Docker": 2.0} and p.mastered == {"Redis"}
    assert p.remediation == {"System Design": 30.0} and p.forced_skills == ["System Design"]
    assert p.start == date(2026, 2, 1) and p.origin == "replanned"


def test_evaluator_detects_problems_and_readiness_is_explainable():
    report, plan = _plan(10, 8)
    ev = CareerEvaluationAgent().evaluate(
        analysis=None,
        gaps=report,
        plan=plan,
        known_resource_ids={7},
        assessments={},
        previous_readiness=None,
        progress=(0, plan.planned_hours),
    )
    assert ev.valid, ev.issues
    # Corrupt the plan: drop the top gap, reference an unknown resource, add an untraceable task.
    top = max(
        (g for g in report.items if g.status != GapStatus.matched and g.requirement_level == R),
        key=lambda g: g.priority_score,
    ).skill
    bad = plan.model_copy(deep=True)
    bad.covered_skills = [s for s in bad.covered_skills if s != top]
    bad.weeks[0].tasks[0].resource_ids = [999]
    bad.weeks[0].tasks[0].skill = "Rust"
    ev2 = CareerEvaluationAgent().evaluate(
        analysis=None,
        gaps=report,
        plan=bad,
        known_resource_ids={7},
        assessments={"Python": 40},
        previous_readiness=ev.readiness.score,
        progress=(5, plan.planned_hours),
    )
    codes = {i.code for i in ev2.issues}
    assert not ev2.valid and {"top_gap_uncovered", "unknown_resource", "task_without_gap"} <= codes
    comps = {c.name: c for c in ev2.readiness.components}
    assert comps["assessments"].value == 40 and ev2.readiness.delta is not None
    assert "not a probability" in ev2.readiness.disclaimer


def test_readiness_renormalizes_missing_components():
    report = _gaps()
    r = CareerEvaluationAgent.readiness(report, {}, None, None)
    expected = (0.4 * 100 * report.coverage + 0.2 * r.components[1].value) / 0.6
    assert r.score == pytest.approx(expected, abs=0.11)


def test_resolve_role():
    assert resolve_role("Senior Python Backend Engineer").key == "backend_engineer"
    assert resolve_role("SRE").key == "devops_engineer"
    assert resolve_role("Underwater basket weaver") is None
