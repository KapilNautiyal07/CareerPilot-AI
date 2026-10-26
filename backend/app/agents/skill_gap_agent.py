"""Skill Gap Analysis Agent.

Aggregates demand across the candidate's most relevant jobs and blends it with the target-role
baseline (demand_share = 0.7 x share of jobs + 0.3 x role baseline, where the baseline is 1.0 for
role-required and 0.5 for role-preferred skills; with fewer than MIN_JOBS listings the baseline is
used alone and labeled as such). Every demanded skill is classified matched / partial / missing
with evidence and a transparent priority score:

    importance     = level_weight (required 1.0, preferred 0.5) x sqrt(demand_share)
                     x 0.5 for soft skills (resumes rarely state them, so "missing" is weak evidence;
                     they are assessed through behavioral interviews instead)
    severity       = 1 - credit                      (credit from services.matching)
    urgency        = required: 1 + 0.5 x tightness  | preferred: 1 - 0.3 x tightness
                     tightness = clamp((12 - weeks_until_deadline) / 12, 0, 1)
    effort_penalty = 1 - 0.3 x min(1, hours_needed / (0.5 x capacity_hours))
    priority       = 100 x importance x severity x urgency x effort_penalty   (capped at 100)
"""

from __future__ import annotations

import math
from collections import defaultdict

from app.models.enums import EvidenceLevel, GapStatus, RequirementLevel
from app.schemas.agents import PriorityComponents, SkillGapItem, SkillGapReport, SkillRequirement
from app.services import skills as tax
from app.services.matching import CandidateSkill, skill_credit
from app.services.roles import RoleTemplate

MIN_JOBS = 3
MIN_DEMAND_SHARE = 0.15
SOFT_IMPORTANCE = 0.5


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


class SkillGapAgent:
    name = "skill_gap"

    def analyze(
        self,
        *,
        target_role: str,
        role: RoleTemplate | None,
        job_requirements: list[list[SkillRequirement]],
        have: dict[str, CandidateSkill],
        weeks_until_deadline: float | None,
        weekly_hours: float | None,
    ) -> SkillGapReport:
        demand = self._demand(job_requirements, role)
        n_jobs = len(job_requirements)
        source = "jobs" if role is None else ("jobs+role_template" if n_jobs else "role_template")
        capacity = (weeks_until_deadline or 12) * (weekly_hours or 8)
        tightness = _clamp((12 - weeks_until_deadline) / 12) if weeks_until_deadline is not None else 0.0

        items: list[SkillGapItem] = []
        imp_total = imp_gained = 0.0
        for skill, (level, share, evidence_req) in demand.items():
            credit, status, own, via = skill_credit(skill, have)
            # Components are rounded first so the displayed priority is exactly reproducible from them.
            soft = SOFT_IMPORTANCE if tax.category_of(skill) == "soft" else 1.0
            importance = round((1.0 if level == RequirementLevel.required else 0.5) * math.sqrt(share) * soft, 3)
            severity = round(1 - credit, 3)
            urgency = round(1 + 0.5 * tightness if level == RequirementLevel.required else 1 - 0.3 * tightness, 3)
            hours_needed = round(tax.learn_hours(skill) * severity, 1)
            effort_penalty = round(1 - 0.3 * min(1.0, hours_needed / max(1.0, 0.5 * capacity)), 3)
            priority = min(100.0, 100 * importance * severity * urgency * effort_penalty)
            imp_total += importance
            imp_gained += importance * credit
            transferable = (
                []
                if status == GapStatus.matched
                else ([via] if via else [n for n in have if skill in tax.related_skills(n)][:3])
            )
            evidence = list(own.evidence[:3]) if own else []
            if own and own.assessed_score is not None:
                evidence.insert(0, f"Latest CareerPilot assessment: {own.assessed_score:.0f}/100")
            evidence.extend(evidence_req[:2])
            items.append(
                SkillGapItem(
                    skill=skill,
                    category=tax.category_of(skill),
                    status=status,
                    requirement_level=level,
                    demand_share=round(share, 3),
                    evidence_strength=round(credit, 3),
                    evidence=evidence,
                    priority_score=round(priority, 1),
                    components=PriorityComponents(
                        importance=importance, severity=severity, urgency=urgency, effort_penalty=effort_penalty
                    ),
                    estimated_hours=hours_needed if status != GapStatus.matched else 0.0,
                    transferable_from=[t for t in transferable if t],
                    recommendation=self._recommend(skill, status, own, via, level),
                )
            )
        items.sort(key=lambda i: (-i.priority_score, i.skill))
        nxt = [i.skill for i in items if i.status != GapStatus.matched][:5]
        return SkillGapReport(
            target_role=target_role or (role.title if role else ""),
            demand_source=source,
            jobs_considered=n_jobs,
            weeks_until_deadline=weeks_until_deadline,
            items=items,
            recommended_next=nxt,
            coverage=round(imp_gained / imp_total, 3) if imp_total else 0.0,
        )

    @staticmethod
    def _demand(
        job_requirements: list[list[SkillRequirement]], role: RoleTemplate | None
    ) -> dict[str, tuple[RequirementLevel, float, list[str]]]:
        """skill -> (requirement level, share of jobs demanding it, sample requirement texts)."""
        mentions: dict[str, int] = defaultdict(int)
        required: dict[str, int] = defaultdict(int)
        texts: dict[str, list[str]] = defaultdict(list)
        for reqs in job_requirements:
            for r in reqs:
                mentions[r.skill] += 1
                required[r.skill] += r.level == RequirementLevel.required
                if len(texts[r.skill]) < 2 and r.evidence_text:
                    texts[r.skill].append(f'Job requirement: "{r.evidence_text[:160]}"')
        n = len(job_requirements)
        out: dict[str, tuple[RequirementLevel, float, list[str]]] = {}
        for skill, count in mentions.items():
            share = count / n
            if share < MIN_DEMAND_SHARE and n >= MIN_JOBS and role is None:
                continue
            level = RequirementLevel.required if required[skill] / count >= 0.5 else RequirementLevel.preferred
            out[skill] = (level, share, texts[skill])
        if role is None:
            return out
        baseline = {**{sk: 0.5 for sk in role.preferred}, **{sk: 1.0 for sk in role.required}}
        if n < MIN_JOBS:
            # Too few real listings: use the role baseline alone, clearly labeled as such.
            for skill, prior in baseline.items():
                lvl, share, t = out.get(skill, (None, 0.0, []))
                level = RequirementLevel.required if prior == 1.0 else (lvl or RequirementLevel.preferred)
                label = "required" if prior == 1.0 else "preferred"
                out[skill] = (
                    level,
                    max(share, 0.8 * prior),
                    t or [f"Commonly {label} for {role.title} roles (role baseline)"],
                )
            return out
        blended: dict[str, tuple[RequirementLevel, float, list[str]]] = {}
        for skill in set(out) | set(baseline):
            lvl, share, t = out.get(skill, (None, 0.0, []))
            prior = baseline.get(skill, 0.0)
            level = lvl or (RequirementLevel.required if prior == 1.0 else RequirementLevel.preferred)
            if prior == 1.0:
                level = RequirementLevel.required
            mixed = 0.7 * share + 0.3 * prior
            if mixed < MIN_DEMAND_SHARE:
                continue
            if not t:
                t = [f"Core skill for {role.title} roles (role baseline)"]
            blended[skill] = (level, mixed, t)
        return blended
        return out

    @staticmethod
    def _recommend(
        skill: str, status: GapStatus, own: CandidateSkill | None, via: str | None, level: RequirementLevel
    ) -> str:
        if status == GapStatus.matched:
            return f"Keep {skill} sharp; mention concrete outcomes in interviews."
        if own and own.assessed_score is not None:
            return (
                f"Your last assessment on {skill} scored {own.assessed_score:.0f}/100. Revise the concepts you "
                f"missed and re-test with a focused mock interview."
            )
        if own and own.evidence_level == EvidenceLevel.listed:
            return f"You list {skill} but show no usage. Build a small project that uses it and add it to your resume."
        if own and own.evidence_level == EvidenceLevel.inferred:
            return (
                f"{skill} is only implied by your experience. Practice it directly and make it explicit on your resume."
            )
        if via:
            return f"Leverage your {via} experience: learn the differences, then build a small {skill} exercise."
        lead = "A core requirement" if level == RequirementLevel.required else "A preferred skill"
        return f"{lead} with no current evidence. Start with fundamentals, then a hands-on project."
