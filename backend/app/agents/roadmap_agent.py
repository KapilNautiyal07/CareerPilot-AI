"""Learning Roadmap Agent: turn a skill-gap report into a capacity-bounded weekly plan.

Scheduling is deterministic:
1. capacity = weeks x weekly_hours. Fixed items (revision, mock interviews, behavioral prep,
   DSA maintenance) are reserved first and scaled to the weekly budget.
2. Gaps are funded in two passes in priority order (forced skills first): a foundation slice
   (12% of the learning budget) per gap for breadth, then top-ups toward the full need. No single
   skill may take more than `max_skill_share` of the budget, so one large gap cannot starve the rest.
3. Each funded skill is broken into learn / practice / project / checkpoint tasks (<= 2.5h each),
   shaped by its evidence (e.g. listed-only skills get "demonstrate it" projects, not basics) and
   its kind (soft skills get practice-in-context and interview-story tasks, never "build" projects).
   Retrieved resources are attached per task: practice resources for practice tasks, and levels
   that fit the gap (beginner material for missing skills, intermediate+ for partial ones).
4. Tasks are packed into study days in order; nothing is scheduled past the deadline.
Replanning reuses the same scheduler from today with completed hours subtracted, mastered skills
dropped, and remediation added for weak assessments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models.enums import GapStatus, TaskType
from app.schemas.agents import PlannedTask, RoadmapPlan, SkillGapItem, WeekPlan

MAX_TASK_HOURS = 2.5
TRACK_SKILLS = {"Data Structures & Algorithms", "System Design", "Behavioral Interviewing"}
RESOURCES_PER_TASK = 2
# Preferred resource levels, best first, by gap status.
LEVEL_ORDER = {
    GapStatus.missing: ("beginner", "all", "intermediate", "advanced"),
    GapStatus.partial: ("intermediate", "all", "advanced", "beginner"),
    GapStatus.matched: ("advanced", "intermediate", "all", "beginner"),
}
SOFT_PRACTICE = {
    "Communication": "Write a short design doc or README for one of your projects and get feedback on it; "
    "explain one technical decision to a non-engineer in two minutes.",
    "Leadership": "Own a small initiative end to end, mentor someone, or lead a code review; "
    "note the decisions you made and their outcome.",
    "Collaboration": "Pair-program or contribute to a team or open-source project; practise giving and "
    "receiving review feedback.",
    "Problem Solving": "Debug one real issue end to end and write up the root cause, the options you "
    "considered and why you chose the fix.",
}


def pick_resources(res: list[dict], task_type: TaskType, status: GapStatus, n: int = RESOURCES_PER_TASK) -> list[int]:
    """Choose resources for a task: verified links first, then fit to task type, then fit to gap level."""
    levels = LEVEL_ORDER[status]

    def key(item: tuple[int, dict]) -> tuple:
        idx, r = item
        is_practice = r.get("resource_type") == "practice"
        type_fit = is_practice if task_type == TaskType.practice else not is_practice
        level = r.get("level") or "all"
        return (
            r.get("verification_status") != "reachable",
            not type_fit,
            levels.index(level) if level in levels else len(levels),
            idx,  # retrieval (similarity) order breaks ties
        )

    return [r["id"] for _, r in sorted(enumerate(res), key=key)[:n]]


@dataclass
class PlanParams:
    start: date
    end: date
    weekly_hours: float
    study_days: int
    target_role: str
    tracks: tuple[str, ...] = ()
    max_skill_share: float = 0.35
    forced_skills: list[str] = field(default_factory=list)
    completed_hours: dict[str, float] = field(default_factory=dict)
    mastered: set[str] = field(default_factory=set)
    remediation: dict[str, float] = field(default_factory=dict)  # skill -> latest score
    origin: str = "generated"


@dataclass
class _Item:
    title: str
    description: str
    task_type: TaskType
    skill: str | None
    hours: float
    resource_ids: list[int]


class RoadmapAgent:
    name = "learning_roadmap"

    def plan(self, gaps: list[SkillGapItem], params: PlanParams, resources: dict[str, list[dict]]) -> RoadmapPlan:
        start, end = params.start, max(params.end, params.start)
        weeks = max(1, min(52, math.ceil(((end - start).days + 1) / 7)))
        weekly = max(1.0, params.weekly_hours)
        days = max(1, min(7, params.study_days))
        capacity = round(weeks * weekly, 1)

        fixed = self._fixed_items(weeks, weekly, params, gaps, resources)
        reserved = sum(h.hours for wk in fixed.values() for h in wk)
        budget = max(0.0, capacity - reserved)

        open_gaps = [g for g in gaps if g.status != GapStatus.matched and g.skill not in params.mastered]
        order = sorted(open_gaps, key=lambda g: (g.skill not in params.forced_skills, -g.priority_score, g.skill))
        cap_per_skill = max(3.0, params.max_skill_share * budget)
        covered, uncovered, partial = [], [], []
        needs: dict[str, float] = {}
        for gap in order:
            need = gap.estimated_hours - params.completed_hours.get(gap.skill, 0.0)
            if gap.skill in params.remediation:
                need = max(need, 4.0)
            if need <= 0.25:
                covered.append(gap.skill)  # already done in an earlier version of the plan
                continue
            needs[gap.skill] = max(2.0, need)
        # Pass 1 (breadth): a foundation slice per gap in priority order, so high-priority gaps
        # further down the list are not starved by large ones above them.
        alloc: dict[str, float] = {}
        base = max(3.0, 0.12 * budget)
        for gap in order:
            need = needs.get(gap.skill)
            if need is None:
                continue
            give = round(min(need, base, cap_per_skill, budget), 1)
            if give < min(3.0, need):
                continue
            alloc[gap.skill] = give
            budget -= give
        # Pass 2 (depth): top up toward the full need, still in priority order and under the cap.
        for gap in order:
            if gap.skill not in alloc or budget < 0.5:
                continue
            extra = round(min(needs[gap.skill], cap_per_skill) - alloc[gap.skill], 1)
            if extra > 0:
                extra = round(min(extra, budget), 1)
                alloc[gap.skill] += extra
                budget -= extra
        stream: list[_Item] = []
        for gap in order:
            if gap.skill not in needs:
                continue
            if gap.skill not in alloc:
                uncovered.append(gap.skill)
                continue
            covered.append(gap.skill)
            if alloc[gap.skill] < needs[gap.skill] - 0.05:
                partial.append(gap.skill)
            stream.extend(self._skill_items(gap, round(alloc[gap.skill], 1), resources.get(gap.skill, []), params))

        week_plans = self._pack(stream, fixed, start, end, weeks, weekly, days, params.origin)
        planned = round(sum(t.hours for w in week_plans for t in w.tasks), 1)
        assumptions = [
            f"{weekly:g} study hours per week across {days} study days.",
            "Hour estimates are rough averages per skill and are adjusted as you complete tasks and assessments.",
            "Linked resources are suggestions; links marked unverified have not been checked by CareerPilot.",
        ]
        if partial:
            assumptions.append(
                "Time-limited coverage (not enough capacity for full depth): " + ", ".join(partial) + "."
            )
        if uncovered:
            assumptions.append("Not covered before the deadline: " + ", ".join(uncovered) + ".")
        return RoadmapPlan(
            title=f"{params.target_role or 'Career'} preparation plan",
            target_role=params.target_role,
            start_date=start,
            end_date=end,
            weekly_hours=weekly,
            study_days_per_week=days,
            capacity_hours=capacity,
            planned_hours=planned,
            weeks=week_plans,
            covered_skills=covered,
            uncovered_gaps=uncovered,
            assumptions=assumptions,
        )

    # ------------------------------------------------------------------ building blocks
    def _fixed_items(
        self,
        weeks: int,
        weekly: float,
        p: PlanParams,
        gaps: list[SkillGapItem],
        resources: dict[str, list[dict]],
    ) -> dict[int, list[_Item]]:
        fixed: dict[int, list[_Item]] = {w: [] for w in range(weeks)}
        open_skills = {g.skill for g in gaps if g.status != GapStatus.matched}
        dsa_res = pick_resources(
            resources.get("Data Structures & Algorithms", []), TaskType.practice, GapStatus.matched
        )
        beh_res = pick_resources(resources.get("Behavioral Interviewing", []), TaskType.practice, GapStatus.missing)
        if "dsa" in p.tracks and "Data Structures & Algorithms" not in open_skills:
            h = round(min(1.0, 0.15 * weekly), 1)
            for w in range(weeks):
                fixed[w].append(
                    _Item(
                        "DSA maintenance: 2-3 timed practice problems",
                        "Keep problem-solving speed up: arrays, hashing, two pointers, trees, graphs.",
                        TaskType.practice,
                        "Data Structures & Algorithms",
                        h,
                        dsa_res,
                    )
                )
        rev_h = round(min(1.5, 0.2 * weekly), 1)
        for w in range(2, weeks, 3):
            fixed[w].append(
                _Item(
                    "Revision session",
                    "Revisit notes and redo one exercise for each skill studied in "
                    "the last three weeks. Note anything you could not recall.",
                    TaskType.revision,
                    None,
                    rev_h,
                    [],
                )
            )
        mock_h = round(min(1.5, 0.4 * weekly), 1)
        mock_weeks = sorted({*range(2, weeks, 3), weeks - 1}) if weeks >= 2 else [0]
        for w in mock_weeks:
            fixed[w].append(
                _Item(
                    "Mock interview",
                    "Run a CareerPilot mock interview on this period's focus areas and review the feedback.",
                    TaskType.mock_interview,
                    None,
                    mock_h,
                    [],
                )
            )
        if "behavioral" in p.tracks:
            w = min(weeks - 1, weeks // 2)
            beh = round(min(2.0, 0.25 * weekly), 1)
            fixed[w].append(
                _Item(
                    "Prepare STAR stories",
                    "Write 5-6 stories (Situation, Task, Action, Result) "
                    "covering conflict, failure, leadership, impact and ambiguity, using real examples.",
                    TaskType.practice,
                    "Behavioral Interviewing",
                    beh,
                    beh_res,
                )
            )
            fixed[min(weeks - 1, w + 1)].append(
                _Item(
                    "Behavioral answers out loud",
                    "Practice 3 stories aloud in under 2 minutes each, then run a behavioral mock interview.",
                    TaskType.practice,
                    "Behavioral Interviewing",
                    beh,
                    beh_res,
                )
            )
        return fixed

    def _skill_items(self, gap: SkillGapItem, hours: float, res: list[dict], p: PlanParams) -> list[_Item]:
        s = gap.skill
        soft = gap.category == "soft"
        checkpoint = 0.5 if hours >= 4 else 0.0
        body = hours - checkpoint
        if s in p.remediation:
            parts = [
                (
                    TaskType.practice,
                    f"Targeted practice: {s}",
                    f"Your last assessment scored {p.remediation[s]:.0f}/100. Rework the missed concepts with "
                    f"hands-on exercises.",
                    1.0,
                )
            ]
        elif soft:
            # Soft skills are shown through behaviour and stories, not built into projects.
            parts = [
                (
                    TaskType.learn,
                    f"Study {s} techniques",
                    f"Read about practical {s} techniques for engineers; note two you will try this week.",
                    0.3,
                ),
                (
                    TaskType.practice,
                    f"Practise {s} in real work",
                    SOFT_PRACTICE.get(s, f"Apply {s} in a real team or project setting and note what worked."),
                    0.4,
                ),
                (
                    TaskType.practice,
                    f"Prepare {s} examples",
                    f"Write two STAR stories that show {s}, and add one resume bullet with a concrete outcome.",
                    0.3,
                ),
            ]
        elif gap.status == GapStatus.partial and gap.evidence_strength >= 0.5:
            # Listed-only / weak evidence: prove it rather than relearn basics.
            parts = [
                (TaskType.practice, f"Strengthen {s}", f"Fill gaps in {s} with focused exercises.", 0.4),
                (
                    TaskType.project,
                    f"Demonstrate {s} in a project",
                    f"Build or extend a small project that clearly uses {s}; add a resume bullet with the outcome.",
                    0.6,
                ),
            ]
        elif gap.transferable_from:
            via = gap.transferable_from[0]
            parts = [
                (
                    TaskType.learn,
                    f"Learn {s} (coming from {via})",
                    f"Map what you know from {via} to {s}; focus on the differences.",
                    0.3,
                ),
                (TaskType.practice, f"Practice {s}", f"Hands-on exercises in {s}.", 0.4),
                (
                    TaskType.project,
                    f"Mini project with {s}",
                    f"Re-implement something you built with {via} using {s}.",
                    0.3,
                ),
            ]
        elif body >= 6:
            parts = [
                (TaskType.learn, f"Learn {s} fundamentals", f"Study core concepts of {s}; take notes.", 0.4),
                (TaskType.practice, f"Practice {s}", f"Work through exercises applying {s}.", 0.35),
                (TaskType.project, f"Build with {s}", f"Small project that uses {s} end to end.", 0.25),
            ]
        else:
            parts = [
                (TaskType.learn, f"Learn {s} fundamentals", f"Study core concepts of {s}.", 0.5),
                (TaskType.practice, f"Practice {s}", f"Exercises applying {s}.", 0.5),
            ]
        items: list[_Item] = []
        for ttype, title, desc, frac in parts:
            h = round(body * frac, 1)
            n = max(1, math.ceil(h / MAX_TASK_HOURS))
            for i in range(n):
                chunk = round(h / n, 1)
                if chunk <= 0:
                    continue
                suffix = f" ({i + 1}/{n})" if n > 1 else ""
                items.append(
                    _Item(
                        title + suffix,
                        desc,
                        ttype,
                        s,
                        chunk,
                        pick_resources(res, ttype, gap.status) if ttype in (TaskType.learn, TaskType.practice) else [],
                    )
                )
        if checkpoint:
            items.append(
                _Item(
                    f"Checkpoint: {s}",
                    f"Run a behavioral mock interview and check that your {s} stories are specific and "
                    f"end with a result."
                    if soft
                    else f"Self-check: explain the key ideas of {s} without notes, or take a "
                    f"short CareerPilot mock interview covering it.",
                    TaskType.assessment,
                    s,
                    checkpoint,
                    [],
                )
            )
        return items

    def _pack(
        self,
        stream: list[_Item],
        fixed: dict[int, list[_Item]],
        start: date,
        end: date,
        weeks: int,
        weekly: float,
        days: int,
        origin: str,
    ) -> list[WeekPlan]:
        plans: list[WeekPlan] = []
        queue = list(stream)
        per_day = weekly / days
        for w in range(weeks):
            wk_start = start + timedelta(days=7 * w)
            wk_end = min(end, wk_start + timedelta(days=6))
            study_days = [wk_start + timedelta(days=d) for d in range(days) if wk_start + timedelta(days=d) <= wk_end]
            if not study_days:
                study_days = [wk_end]
            remaining = weekly
            placed: list[_Item] = []
            for item in fixed.get(w, []):
                if item.hours <= remaining + 1e-6:
                    placed.append(item)
                    remaining -= item.hours
            learning: list[_Item] = []
            while queue and remaining > 0.25:
                item = queue[0]
                if item.hours <= remaining + 1e-6:
                    learning.append(queue.pop(0))
                    remaining -= item.hours
                elif remaining >= 0.5:
                    first = _Item(
                        item.title + " (start)",
                        item.description,
                        item.task_type,
                        item.skill,
                        round(remaining, 1),
                        item.resource_ids,
                    )
                    item.hours = round(item.hours - first.hours, 1)
                    item.title = (
                        item.title.replace(" (start)", "") + " (finish)" if "(finish)" not in item.title else item.title
                    )
                    learning.append(first)
                    remaining = 0
                else:
                    break
            # Learning first, fixed items (revision / mock interview) at the end of the week.
            ordered = learning + placed
            tasks: list[PlannedTask] = []
            used = 0.0
            for it in ordered:
                day_idx = min(len(study_days) - 1, int(used / per_day + 1e-6))
                used += it.hours
                tasks.append(
                    PlannedTask(
                        title=it.title[:300],
                        description=it.description,
                        task_type=it.task_type,
                        skill=it.skill,
                        hours=it.hours,
                        scheduled_date=study_days[day_idx],
                        due_date=wk_end,
                        resource_ids=it.resource_ids,
                        origin="remediation" if it.title.startswith("Targeted practice") else origin,
                    )
                )
            focus = list(dict.fromkeys(t.skill for t in tasks if t.skill))
            plans.append(
                WeekPlan(
                    index=w + 1,
                    start=wk_start,
                    end=wk_end,
                    focus=focus,
                    milestone=self._milestone(tasks),
                    planned_hours=round(sum(t.hours for t in tasks), 1),
                    tasks=tasks,
                )
            )
        return plans

    @staticmethod
    def _milestone(tasks: list[PlannedTask]) -> str:
        # A project only split-started this week is not a shipped milestone.
        projects = [
            t.skill for t in tasks if t.task_type == TaskType.project and t.skill and not t.title.endswith("(start)")
        ]
        started = [
            t.skill for t in tasks if t.task_type == TaskType.project and t.skill and t.title.endswith("(start)")
        ]
        checkpoints = [t.skill for t in tasks if t.task_type == TaskType.assessment and t.skill]
        learns = list(
            dict.fromkeys(t.skill for t in tasks if t.task_type in (TaskType.learn, TaskType.practice) and t.skill)
        )
        if projects:
            return "Ship a working project using " + ", ".join(dict.fromkeys(projects))
        if checkpoints:
            return "Pass checkpoints for " + ", ".join(dict.fromkeys(checkpoints))
        if started:
            return "Start a project using " + ", ".join(dict.fromkeys(started))
        if learns:
            return "Solid working knowledge of " + ", ".join(learns[:3])
        if any(t.task_type == TaskType.mock_interview for t in tasks):
            return "Complete a mock interview and review feedback"
        return "Consolidate and review"


def replan_params(
    base: PlanParams,
    *,
    tasks: list[dict],
    assessments: dict[str, float],
    today: date,
    mastery_threshold: float = 80.0,
    remediation_threshold: float = 50.0,
) -> PlanParams:
    """Derive parameters for a replan from task history and latest per-skill assessment scores."""
    completed: dict[str, float] = {}
    for t in tasks:
        if t["status"] == "completed" and t.get("skill"):
            completed[t["skill"]] = completed.get(t["skill"], 0.0) + float(t["estimated_hours"])
    mastered = {s for s, score in assessments.items() if score >= mastery_threshold}
    remediation = {s: score for s, score in assessments.items() if score < remediation_threshold}
    return PlanParams(
        start=max(today, base.start),
        end=base.end,
        weekly_hours=base.weekly_hours,
        study_days=base.study_days,
        target_role=base.target_role,
        tracks=base.tracks,
        max_skill_share=base.max_skill_share,
        forced_skills=list(remediation),
        completed_hours=completed,
        mastered=mastered,
        remediation=remediation,
        origin="replanned",
    )
