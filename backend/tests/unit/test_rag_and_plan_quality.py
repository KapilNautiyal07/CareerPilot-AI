"""Regressions for retrieval and plan quality found by probing beyond the eval set (2026-10-26)."""

from datetime import UTC, date, datetime, timedelta

from app.agents.roadmap_agent import PlanParams, RoadmapAgent, pick_resources
from app.agents.skill_gap_agent import SkillGapAgent
from app.db.session import session_scope
from app.models import LearningResource
from app.models.enums import EvidenceLevel, GapStatus, RequirementLevel, ResourceVerification, TaskType
from app.repositories.career import retrieve_resources
from app.schemas.agents import NormalizedJob, PlannedTask, SkillRequirement
from app.services.embeddings import embed
from app.services.matching import CandidateSkill, Preferences, match_job, score_role
from app.services.roles import BY_KEY

R = RequirementLevel.required


def _res(id_: int, level: str, rtype: str = "docs", status: str = "unverified") -> dict:
    return {"id": id_, "level": level, "resource_type": rtype, "verification_status": status}


# ---------------------------------------------------------------------- job ranking: role fit
def test_role_score_same_adjacent_and_different_roles():
    assert score_role("Senior Data Engineer", "Data Engineer")[0] == 100
    adjacent, note = score_role("Python Backend Developer", "Python Developer")
    assert 50 <= adjacent < 100 and "Adjacent" in note
    assert score_role("Frontend Engineer", "Data Engineer")[0] <= 20
    assert score_role("Backend Engineer", None)[0] == 60  # neutral without a target


def test_same_role_title_outranks_equal_skill_fit():
    reqs = [SkillRequirement(skill="Python", level=R), SkillRequirement(skill="SQL", level=R)]
    have = {s: CandidateSkill(s, EvidenceLevel.demonstrated) for s in ("Python", "SQL")}

    def job(title: str) -> NormalizedJob:
        return NormalizedJob(
            source="mock",
            external_id=title,
            title=title,
            company="X",
            retrieved_at=datetime.now(UTC),
            requirements=reqs,
        )

    data = match_job(1, job("Data Engineer"), have, 3, Preferences(), 30, "Data Engineer")
    backend = match_job(2, job("Backend Engineer"), have, 3, Preferences(), 30, "Data Engineer")
    assert data.skill_score == backend.skill_score and data.score > backend.score


# ---------------------------------------------------------------------- resource selection
def test_pick_resources_fits_gap_level_and_task_type():
    res = [_res(1, "beginner"), _res(2, "intermediate"), _res(3, "all", "practice"), _res(4, "advanced")]
    assert pick_resources(res, TaskType.learn, GapStatus.partial)[0] == 2
    assert pick_resources(res, TaskType.learn, GapStatus.missing)[0] == 1
    assert pick_resources(res, TaskType.practice, GapStatus.missing)[0] == 3


def test_pick_resources_prefers_verified_links():
    res = [_res(1, "beginner"), _res(2, "advanced", status="reachable")]
    assert pick_resources(res, TaskType.learn, GapStatus.missing)[0] == 2


def test_retrieval_filters_by_skill_before_similarity_limit():
    """A skill's resources must not be crowded out by many nearer resources tagged with other skills."""
    with session_scope() as db:
        for i in range(60):
            text = f"Kafka streaming tutorial part {i}"
            db.add(
                LearningResource(
                    title=text,
                    url=f"https://example.com/d{i}",
                    skills=["Spark"],
                    description=text,
                    embedding=embed(text),
                )
            )
        db.add(
            LearningResource(
                title="Event logs",
                url="https://example.com/k",
                skills=["Kafka"],
                description="",
                embedding=embed("logs"),
            )
        )
        db.add(
            LearningResource(
                title="Dead link",
                url="https://example.com/dead",
                skills=["Kafka"],
                description="",
                embedding=embed("Kafka streaming"),
                verification_status=ResourceVerification.unreachable,
            )
        )
        db.flush()
        found = retrieve_resources(db, ["Kafka"], context="Kafka streaming")
    assert [r["url"] for r in found["Kafka"]] == ["https://example.com/k"]
    assert found["Kafka"][0]["level"] == "all"


# ---------------------------------------------------------------------- soft skills and plan shape
def _report(have: dict[str, CandidateSkill] | None = None):
    return SkillGapAgent().analyze(
        target_role="Backend Engineer",
        role=BY_KEY["backend_engineer"],
        job_requirements=[],
        have=have or {"Python": CandidateSkill("Python", EvidenceLevel.demonstrated)},
        weeks_until_deadline=12,
        weekly_hours=8,
    )


def test_soft_skill_importance_is_damped():
    items = {g.skill: g for g in _report().items}
    # Both are role-preferred with the same baseline share; the soft skill counts half.
    assert items["Communication"].components.importance == round(items["Observability"].components.importance / 2, 3)


def _plan(report, resources=None, weeks=12):
    start = date(2026, 11, 2)
    params = PlanParams(
        start=start,
        end=start + timedelta(weeks=weeks) - timedelta(days=1),
        weekly_hours=8,
        study_days=5,
        target_role="Backend Engineer",
        tracks=BY_KEY["backend_engineer"].interview_tracks,
        forced_skills=["Communication"],
    )
    return RoadmapAgent().plan(report.items, params, resources or {})


def test_soft_skills_get_story_tasks_not_projects():
    plan = _plan(_report())
    comm = [t for w in plan.weeks for t in w.tasks if t.skill == "Communication"]
    assert comm and not [t for t in comm if t.task_type == TaskType.project]
    assert any(t.title.startswith("Prepare Communication examples") for t in comm)
    assert not any("Communication" in w.milestone and w.milestone.startswith("Ship") for w in plan.weeks)


def test_fixed_prep_tasks_get_resources():
    dsa = {"Data Structures & Algorithms": CandidateSkill("Data Structures & Algorithms", EvidenceLevel.demonstrated)}
    report = _report({"Python": CandidateSkill("Python", EvidenceLevel.demonstrated), **dsa})
    resources = {
        "Data Structures & Algorithms": [_res(11, "all", "practice"), _res(12, "advanced", "article")],
        "Behavioral Interviewing": [_res(21, "all", "article")],
    }
    tasks = [t for w in _plan(report, resources).weeks for t in w.tasks]
    maintenance = [t for t in tasks if t.title.startswith("DSA maintenance")]
    star = [t for t in tasks if t.title == "Prepare STAR stories"]
    assert maintenance and all(t.resource_ids[0] == 11 for t in maintenance)
    assert star and star[0].resource_ids == [21]


def test_split_project_week_is_not_a_shipped_milestone():
    t = PlannedTask(
        title="Build with Kafka (start)",
        description="",
        task_type=TaskType.project,
        skill="Kafka",
        hours=1.0,
        scheduled_date=date(2026, 11, 2),
        due_date=date(2026, 11, 8),
    )
    assert RoadmapAgent._milestone([t]) == "Start a project using Kafka"
