"""Deterministic, explainable candidate-to-job scoring.

Skill credit per requirement (0-1):
  assessed in-app        -> latest assessment score / 100 (a low score lowers credit)
  demonstrated in resume -> 0.90
  user asserted          -> 0.70
  listed only            -> 0.55   (a keyword is not proof of proficiency)
  inferred               -> 0.35
  related skill only     -> 0.5 x credit of the related skill (partial / transferable)
  none                   -> 0

skill_score      = 100 * (sum(w * credit) + 2 * 0.5) / (sum(w) + 2),  w = 1.0 required, 0.4 preferred
                   (the +2 pseudo-weight at 50% shrinks listings with very few extracted requirements
                    toward neutral, so a 3-line posting cannot outrank a detailed one on sparsity alone)
experience_score = 100 if years >= min_years, else proportional; neutral when unknown
preference_score = 100 minus penalties for arrangement / location / salary mismatches
recency_score    = 100 within a week, decaying to 0 at 2x the stale threshold
role_score       = how well the job title fits the target role: 100 same role template, partial for
                   adjacent roles (e.g. Backend <-> Python Developer), 15 for a different role
overall          = 0.50 skill + 0.15 role + 0.12 experience + 0.13 preference + 0.10 recency
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.agents.resume_agent import EVIDENCE_WEIGHT
from app.models.enums import EvidenceLevel, GapStatus, RequirementLevel, WorkArrangement
from app.schemas.agents import JobMatchScore, NormalizedJob, SkillMatchDetail, SkillRequirement
from app.services import skills as tax
from app.services.job_parsing import location_fits
from app.services.roles import resolve_role

REQ_WEIGHT = {RequirementLevel.required: 1.0, RequirementLevel.preferred: 0.4}
MATCHED_THRESHOLD = 0.75
WEIGHTS = {"skill": 0.50, "role": 0.15, "experience": 0.12, "preference": 0.13, "recency": 0.10}
PRIOR_WEIGHT, PRIOR_CREDIT = 2.0, 0.5


@dataclass
class CandidateSkill:
    skill: str
    evidence_level: EvidenceLevel
    assessed_score: float | None = None  # 0-100 latest in-app assessment
    evidence: list[str] = field(default_factory=list)

    @property
    def credit(self) -> float:
        if self.assessed_score is not None:
            return max(0.05, min(1.0, self.assessed_score / 100))
        return EVIDENCE_WEIGHT[self.evidence_level]


@dataclass
class Preferences:
    locations: list[str] = field(default_factory=list)
    arrangements: list[str] = field(default_factory=list)
    salary_min: int | None = None
    salary_currency: str | None = None


def skill_credit(
    skill: str, have: dict[str, CandidateSkill]
) -> tuple[float, GapStatus, CandidateSkill | None, str | None]:
    """Return (credit, status, own evidence, related skill used)."""
    own = have.get(skill)
    if own is not None:
        c = own.credit
        return c, (GapStatus.matched if c >= MATCHED_THRESHOLD else GapStatus.partial), own, None
    best: tuple[float, str] | None = None
    for rel in tax.related_skills(skill):
        r = have.get(rel)
        if r is not None:
            c = 0.5 * r.credit
            if best is None or c > best[0]:
                best = (c, rel)
    # Reverse relation: the candidate's skill lists this one as related.
    for name, r in have.items():
        if skill in tax.related_skills(name):
            c = 0.5 * r.credit
            if best is None or c > best[0]:
                best = (c, name)
    if best:
        return best[0], GapStatus.partial, None, best[1]
    return 0.0, GapStatus.missing, None, None


def explain(skill: str, credit: float, status: GapStatus, own: CandidateSkill | None, via: str | None) -> str:
    if own is not None:
        if own.assessed_score is not None:
            return f"Assessed in CareerPilot at {own.assessed_score:.0f}/100."
        return {
            EvidenceLevel.demonstrated: "Used in described work or projects on your resume.",
            EvidenceLevel.listed: "Only listed on your resume; no described usage, so treated as partial.",
            EvidenceLevel.inferred: "Inferred from a related skill on your resume, not stated directly.",
            EvidenceLevel.user_asserted: "Added by you; not yet demonstrated or assessed.",
            EvidenceLevel.assessed: "Assessed in CareerPilot.",
        }[own.evidence_level]
    if via:
        return f"Not found on your resume; partial credit from related skill {via}."
    return "No evidence found on your resume or in assessments."


def score_skills(
    requirements: list[SkillRequirement], have: dict[str, CandidateSkill]
) -> tuple[float, list[SkillMatchDetail]]:
    if not requirements:
        return 0.0, []
    total = gained = 0.0
    details = []
    for req in requirements:
        w = REQ_WEIGHT[req.level]
        credit, status, own, via = skill_credit(req.skill, have)
        total += w
        gained += w * credit
        details.append(
            SkillMatchDetail(
                skill=req.skill,
                level=req.level,
                status=status,
                credit=round(credit, 3),
                evidence_level=own.evidence_level if own else None,
                via=via,
                explanation=explain(req.skill, credit, status, own, via),
            )
        )
    return round(100 * (gained + PRIOR_WEIGHT * PRIOR_CREDIT) / (total + PRIOR_WEIGHT), 1), details


def score_experience(candidate_years: float | None, min_years: float | None) -> tuple[float, str | None]:
    if min_years is None:
        return 70.0, "Job does not state required years of experience."
    if candidate_years is None:
        return 50.0, "Your years of experience are unknown; add dated roles to improve accuracy."
    if candidate_years >= min_years:
        return 100.0, None
    return round(
        100 * candidate_years / min_years, 1
    ), f"Asks for {min_years:g}+ years; you have about {candidate_years:g}."


def score_preferences(job: NormalizedJob, prefs: Preferences) -> tuple[float, list[str]]:
    score, notes = 100.0, []
    if (
        prefs.arrangements
        and job.work_arrangement != WorkArrangement.unknown
        and job.work_arrangement.value not in prefs.arrangements
    ):
        score -= 45
        notes.append(f"Work arrangement is {job.work_arrangement.value}; you prefer {', '.join(prefs.arrangements)}.")
    if prefs.locations and job.location and not location_fits(job.location, prefs.locations):
        score -= 30
        if "remote" in job.location.lower():
            notes.append(f"Remote role limited to {job.location}, outside your preferred locations.")
        else:
            notes.append(f"Location {job.location} is outside your preferred locations.")
    if (
        prefs.salary_min
        and job.salary_max
        and (not job.salary_currency or job.salary_currency == prefs.salary_currency)
    ):
        if job.salary_max < prefs.salary_min:
            score -= 25
            notes.append("Posted salary range is below your minimum.")
    return max(0.0, score), notes


# Adjacent roles share most of their skill demand; symmetric, keyed by sorted role-key pairs.
ROLE_AFFINITY = {
    ("backend_engineer", "python_developer"): 0.7,
    ("backend_engineer", "full_stack_engineer"): 0.6,
    ("frontend_engineer", "full_stack_engineer"): 0.6,
    ("data_engineer", "ml_engineer"): 0.4,
    ("backend_engineer", "devops_engineer"): 0.3,
    ("backend_engineer", "data_engineer"): 0.3,
    ("data_engineer", "python_developer"): 0.3,
}
_GENERIC_TITLE_WORDS = {
    "senior",
    "junior",
    "lead",
    "staff",
    "principal",
    "engineer",
    "developer",
    "ii",
    "iii",
    "i",
    "sr",
    "jr",
}


def score_role(job_title: str, target_role: str | None) -> tuple[float, str | None]:
    """Fit of a job title to the candidate's target role (neutral when no target is set)."""
    if not target_role or not job_title:
        return 60.0, None
    target, job = resolve_role(target_role), resolve_role(job_title)
    if target and job:
        if target.key == job.key:
            return 100.0, None
        aff = ROLE_AFFINITY.get(tuple(sorted((target.key, job.key))), 0.0)  # type: ignore[arg-type]
        if aff:
            return round(100 * aff, 1), f"Adjacent role to your target ({target.title})."
        return 15.0, f"Different role from your target ({target.title})."
    # One side has no template: fall back to overlap of meaningful title words.
    words = lambda t: set(re.findall(r"[a-z+#.]+", t.lower())) - _GENERIC_TITLE_WORDS  # noqa: E731
    if words(target_role) & words(job_title):
        return 70.0, None
    return 35.0, f"Title does not clearly match your target role ({target_role})."


def score_recency(posted_at: datetime | None, stale_after_days: int, now: datetime | None = None) -> float:
    if posted_at is None:
        return 40.0
    days = ((now or datetime.now(UTC)) - posted_at).days
    if days <= 7:
        return 100.0
    horizon = 2 * stale_after_days
    return round(max(0.0, 100 * (1 - (days - 7) / max(1, horizon - 7))), 1)


def match_job(
    job_id: int,
    job: NormalizedJob,
    have: dict[str, CandidateSkill],
    years: float | None,
    prefs: Preferences,
    stale_after_days: int,
    target_role: str | None = None,
) -> JobMatchScore:
    skill_score, details = score_skills(job.requirements, have)
    role_score, role_note = score_role(job.title, target_role)
    exp_score, exp_note = score_experience(years, job.min_years_experience)
    pref_score, notes = score_preferences(job, prefs)
    recency = score_recency(job.posted_at, stale_after_days)
    overall = (
        WEIGHTS["skill"] * skill_score
        + WEIGHTS["role"] * role_score
        + WEIGHTS["experience"] * exp_score
        + WEIGHTS["preference"] * pref_score
        + WEIGHTS["recency"] * recency
    )
    if exp_note:
        notes.insert(0, exp_note)
    if role_note:
        notes.insert(0, role_note)
    if not job.requirements:
        notes.append("No skill requirements could be extracted; skill score is 0.")
    if "stale" in job.quality_flags:
        notes.append("Listing is older than the staleness threshold and may be closed.")
    return JobMatchScore(
        job_id=job_id,
        score=round(overall, 1),
        skill_score=skill_score,
        experience_score=exp_score,
        preference_score=pref_score,
        recency_score=recency,
        role_score=role_score,
        details=details,
        notes=notes,
    )
