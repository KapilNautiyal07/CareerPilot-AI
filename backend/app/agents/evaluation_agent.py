"""Career Evaluation Agent.

Validates the outputs of the other agents against each other and against the evidence, and
computes an explainable readiness indicator. Errors make the output invalid and trigger a
bounded revision; warnings are reported but do not block.

Readiness (0-100) = weighted mean of available components (weights renormalized when a
component has no data yet, and that is stated explicitly):
  skill coverage        0.40  importance-weighted share of demanded skills matched (from gap analysis)
  evidence strength     0.20  mean credit over required skills (demonstrated > listed > inferred)
  assessment results    0.25  mean of the latest in-app assessment score per competency
  plan progress         0.15  completed / planned roadmap hours
"""

from __future__ import annotations

from app.models.enums import EvidenceLevel, GapStatus, RequirementLevel, TaskType
from app.schemas.agents import (
    EvaluationIssue,
    EvaluationReport,
    ReadinessComponent,
    ReadinessReport,
    ResumeAnalysis,
    RoadmapPlan,
    SkillGapReport,
)
from app.services import skills as tax

TRACK_SKILLS = {"Data Structures & Algorithms", "System Design", "Behavioral Interviewing"}
READINESS_WEIGHTS = {"skill_coverage": 0.40, "evidence_strength": 0.20, "assessments": 0.25, "plan_progress": 0.15}


class CareerEvaluationAgent:
    name = "career_evaluation"

    def evaluate(
        self,
        *,
        analysis: ResumeAnalysis | None,
        gaps: SkillGapReport,
        plan: RoadmapPlan | None,
        known_resource_ids: set[int],
        assessments: dict[str, float],
        previous_readiness: float | None,
        progress: tuple[float, float] | None = None,
    ) -> EvaluationReport:
        issues: list[EvaluationIssue] = []
        checks: list[dict] = []

        def check(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})

        gap_by_skill = {g.skill: g for g in gaps.items}
        open_required = [
            g for g in gaps.items if g.status != GapStatus.matched and g.requirement_level == RequirementLevel.required
        ]

        # 1. Resume claims must be backed by evidence (no unsupported claims propagate downstream).
        if analysis is not None:
            unsupported = [s.skill for s in analysis.skills if not s.evidence]
            check("resume_skills_have_evidence", not unsupported, f"{len(unsupported)} skill(s) without evidence")
            for s in unsupported:
                issues.append(
                    EvaluationIssue(
                        code="unsupported_skill",
                        severity="error",
                        skill=s,
                        message=f"{s} was extracted without any supporting evidence.",
                    )
                )
            claimed = {s.skill for s in analysis.skills}
            summary_mentions = {m.skill for m in tax.find_skills(analysis.summary)} - claimed
            if summary_mentions:
                issues.append(
                    EvaluationIssue(
                        code="summary_unsupported",
                        severity="warning",
                        message="Summary mentions skills not found elsewhere: " + ", ".join(sorted(summary_mentions)),
                    )
                )
            # 2. Contradictions between resume evidence and gap classification.
            demonstrated = {s.skill for s in analysis.skills if s.evidence_level == EvidenceLevel.demonstrated}
            contradictions = [g.skill for g in gaps.items if g.skill in demonstrated and g.status == GapStatus.missing]
            check("no_contradictions", not contradictions, f"{len(contradictions)} contradiction(s)")
            for s in contradictions:
                issues.append(
                    EvaluationIssue(
                        code="contradiction",
                        severity="error",
                        skill=s,
                        message=f"{s} is demonstrated on the resume but classified as missing.",
                    )
                )

        if plan is not None:
            task_skills = {t.skill for w in plan.weeks for t in w.tasks if t.skill}
            # 3. Every task must trace back to a gap or an interview track.
            unsupported_tasks = sorted(task_skills - set(gap_by_skill) - TRACK_SKILLS)
            check("tasks_trace_to_gaps", not unsupported_tasks, f"{len(unsupported_tasks)} untraceable skill(s)")
            for s in unsupported_tasks:
                issues.append(
                    EvaluationIssue(
                        code="task_without_gap",
                        severity="error",
                        skill=s,
                        message=f"Roadmap contains tasks for {s}, which is not an identified gap.",
                    )
                )
            # 4. The most important gaps must be addressed.
            top = sorted(open_required, key=lambda g: -g.priority_score)[:3]
            missing_top = [g.skill for g in top if g.skill not in plan.covered_skills]
            check("top_gaps_covered", not missing_top, f"top required gaps: {[g.skill for g in top]}")
            for s in missing_top:
                issues.append(
                    EvaluationIssue(
                        code="top_gap_uncovered",
                        severity="error",
                        skill=s,
                        message=f"High-priority required gap {s} is not covered by the roadmap.",
                    )
                )
            # 5. Capacity and deadline.
            over_weeks = [w.index for w in plan.weeks if w.planned_hours > plan.weekly_hours * 1.05]
            within = plan.planned_hours <= plan.capacity_hours * 1.02 and not over_weeks
            check("capacity_respected", within, f"{plan.planned_hours}h planned of {plan.capacity_hours}h capacity")
            if not within:
                issues.append(
                    EvaluationIssue(
                        code="capacity_exceeded",
                        severity="error",
                        message=f"Plan exceeds available study time (weeks {over_weeks}).",
                    )
                )
            late = [t.title for w in plan.weeks for t in w.tasks if t.due_date > plan.end_date]
            check("deadline_respected", not late, f"{len(late)} task(s) after deadline")
            if late:
                issues.append(
                    EvaluationIssue(
                        code="past_deadline",
                        severity="error",
                        message=f"{len(late)} task(s) are due after the deadline.",
                    )
                )
            # 6. Learning skills already matched is wasted effort.
            matched = {g.skill for g in gaps.items if g.status == GapStatus.matched}
            wasted = sorted(
                {t.skill for w in plan.weeks for t in w.tasks if t.skill in matched and t.task_type == TaskType.learn}
            )
            if wasted:
                issues.append(
                    EvaluationIssue(
                        code="redundant_learning",
                        severity="warning",
                        message="Plan teaches fundamentals of already matched skills: " + ", ".join(wasted),
                    )
                )
            # 7. Resources must come from the catalog (no invented links).
            unknown = sorted({rid for w in plan.weeks for t in w.tasks for rid in t.resource_ids} - known_resource_ids)
            check("resources_from_catalog", not unknown, f"{len(unknown)} unknown resource id(s)")
            if unknown:
                issues.append(
                    EvaluationIssue(
                        code="unknown_resource",
                        severity="error",
                        message=f"Tasks reference resources not in the catalog: {unknown}",
                    )
                )
            if plan.uncovered_gaps:
                issues.append(
                    EvaluationIssue(
                        code="capacity_limited",
                        severity="warning",
                        message="Not enough study time to cover: " + ", ".join(plan.uncovered_gaps),
                    )
                )

        readiness = self.readiness(gaps, assessments, previous_readiness, progress)
        valid = not any(i.severity == "error" for i in issues)
        return EvaluationReport(
            valid=valid,
            issues=issues,
            checks=checks,
            readiness=readiness,
            explanation=self._explain(gaps, readiness, issues, assessments),
        )

    @staticmethod
    def readiness(
        gaps: SkillGapReport,
        assessments: dict[str, float],
        previous: float | None,
        progress: tuple[float, float] | None,
    ) -> ReadinessReport:
        required = [g for g in gaps.items if g.requirement_level == RequirementLevel.required]
        comps: list[tuple[str, float | None, str]] = [
            (
                "skill_coverage",
                100 * gaps.coverage,
                f"{sum(g.status == GapStatus.matched for g in gaps.items)} of {len(gaps.items)} demanded skills "
                f"matched "
                f"(importance-weighted coverage {gaps.coverage:.0%}).",
            ),
            (
                "evidence_strength",
                100 * sum(g.evidence_strength for g in required) / len(required) if required else None,
                "Average strength of evidence for required skills (assessed/demonstrated count more than listed).",
            ),
            (
                "assessments",
                sum(assessments.values()) / len(assessments) if assessments else None,
                f"Mean of latest scores across {len(assessments)} assessed competencies."
                if assessments
                else "No assessments yet; excluded and weights renormalized.",
            ),
            (
                "plan_progress",
                100 * progress[0] / progress[1] if progress and progress[1] else None,
                f"{progress[0]:g} of {progress[1]:g} planned hours completed."
                if progress and progress[1]
                else "No roadmap yet; excluded and weights renormalized.",
            ),
        ]
        available = [(n, v, e) for n, v, e in comps if v is not None]
        total_w = sum(READINESS_WEIGHTS[n] for n, _, _ in available) or 1.0
        score = sum(READINESS_WEIGHTS[n] * v for n, v, _ in available) / total_w
        components = [
            ReadinessComponent(name=n, weight=READINESS_WEIGHTS[n], value=round(v or 0.0, 1), explanation=e)
            for n, v, e in comps
        ]
        score = round(score, 1)
        return ReadinessReport(
            score=score,
            components=components,
            previous_score=previous,
            delta=round(score - previous, 1) if previous is not None else None,
        )

    @staticmethod
    def _explain(
        gaps: SkillGapReport, r: ReadinessReport, issues: list[EvaluationIssue], assessments: dict[str, float]
    ) -> str:
        parts = [f"Readiness {r.score:.0f}/100"]
        if r.delta is not None:
            parts[0] += f" ({'+' if r.delta >= 0 else ''}{r.delta:.0f} since last evaluation)"
        if gaps.recommended_next:
            parts.append("top priorities: " + ", ".join(gaps.recommended_next[:3]))
        weak = sorted((s for s, v in assessments.items() if v < 60), key=lambda s: assessments[s])[:3]
        if weak:
            parts.append("weakest assessed areas: " + ", ".join(weak))
        errs = [i for i in issues if i.severity == "error"]
        if errs:
            parts.append(f"{len(errs)} validation issue(s) need revision")
        return "; ".join(parts) + "."
