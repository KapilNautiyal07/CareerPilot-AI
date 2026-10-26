"""Interview Coach Agent: question selection, rubric-based evaluation, follow-ups and summaries.

Scoring is rubric-driven and deterministic: each criterion lists concept groups (alternative
phrasings); credit = share of groups present in the answer. Coding questions are graded by
executing tests in the sandbox. An LLM (when configured) may refine concept credit, but a
criterion only earns LLM credit when the LLM quotes the candidate's answer verbatim.
"""

from __future__ import annotations

import json
import random
import re
from functools import lru_cache
from pathlib import Path

from app.schemas.agents import AnswerEvaluation, CodeExecutionResult, CriterionResult, LLMRubricJudgement
from app.services import sandbox
from app.services.llm import LLMProvider
from app.services.roles import resolve_role
from app.services.untrusted import wrap_untrusted

BANK_PATH = Path(__file__).resolve().parents[1] / "tools" / "data" / "question_bank.json"
DEFAULT_TRACKS = ("dsa", "backend", "system_design", "behavioral")
MIN_WORDS = 12
HINT_PENALTY = 5.0


@lru_cache
def question_bank() -> tuple[dict, ...]:
    return tuple(json.loads(BANK_PATH.read_text(encoding="utf-8")))


def bank_item(bank_id: str) -> dict | None:
    return next((q for q in question_bank() if q["id"] == bank_id), None)


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    p = phrase.strip().lower()
    pat = re.escape(p)
    if p[:1].isalnum():
        pat = r"\b" + pat
    if len(p) <= 2 and p[-1:].isalnum():
        pat += r"\b"
    return re.compile(pat, re.I)


def concept_credit(answer: str, groups: list[list[str]]) -> tuple[float, str | None]:
    """Share of concept groups present; returns a verbatim snippet of the first match as evidence."""
    if not groups:
        return 0.0, None
    hit, evidence = 0, None
    for group in groups:
        for phrase in group:
            m = _phrase_regex(phrase).search(answer)
            if m:
                hit += 1
                if evidence is None:
                    s = max(0, m.start() - 40)
                    evidence = answer[s : m.end() + 60].strip()
                break
    return hit / len(groups), evidence


class InterviewCoachAgent:
    name = "interview_coach"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    # ------------------------------------------------------------------ selection
    def select_questions(
        self,
        *,
        category: str,
        target_role: str,
        count: int,
        weak_skills: list[str],
        recent_bank_ids: set[str],
        seed: int,
        difficulty: str | None = None,
    ) -> list[dict]:
        rng = random.Random(seed)
        if category == "mixed":
            role = resolve_role(target_role)
            tracks = list(role.interview_tracks) if role else list(DEFAULT_TRACKS)
        else:
            tracks = [category]
        weak = set(weak_skills)
        pools: dict[str, list[dict]] = {}
        for t in tracks:
            items = [q for q in question_bank() if q["category"] == t]
            if difficulty:
                items = sorted(items, key=lambda q: q["difficulty"] != difficulty)

            def rank(q: dict) -> tuple:
                return (
                    -2 * len(weak & set(q["skills"]))
                    + (3 if q["id"] in recent_bank_ids else 0)
                    + (0.5 if difficulty and q["difficulty"] != difficulty else 0),
                    rng.random(),
                )

            pools[t] = sorted(items, key=rank)
        chosen: list[dict] = []
        while len(chosen) < count and any(pools.values()):
            for t in tracks:
                if pools[t] and len(chosen) < count:
                    chosen.append(pools[t].pop(0))
        return chosen

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, question: dict, answer: str, code: str | None = None, hints_used: int = 0) -> AnswerEvaluation:
        answer = (answer or "").strip()
        text = answer + ("\n" + code if code else "")
        code_result: CodeExecutionResult | None = None
        if question.get("code_spec"):
            code_result = (
                sandbox.run_tests(code or "", question["code_spec"])
                if code
                else CodeExecutionResult(
                    status="error", total=len(question["code_spec"]["tests"]), detail="No code was submitted."
                )
            )

        criteria: list[CriterionResult] = []
        for c in question["rubric"]:
            if c.get("type") == "tests":
                assert code_result is not None
                met = (code_result.passed / code_result.total) if code_result.total else 0.0
                ev = (
                    f"{code_result.passed}/{code_result.total} tests passed"
                    if code_result.status in ("passed", "failed")
                    else code_result.detail
                )
                criteria.append(
                    CriterionResult(
                        id=c["id"], description=c["description"], weight=c["weight"], met=round(met, 3), evidence=ev
                    )
                )
            else:
                met, ev = concept_credit(text, c.get("concepts", []))
                criteria.append(
                    CriterionResult(
                        id=c["id"], description=c["description"], weight=c["weight"], met=round(met, 3), evidence=ev
                    )
                )
        evaluator = "rubric_v1"
        if answer and self._refine_with_llm(question, answer, criteria):
            evaluator = "rubric_v1+llm_grounded"

        # Correctness is excluded (and the weights renormalized) when tests could not run.
        scored = [
            c
            for c in criteria
            if not (code_result and code_result.status in ("not_executed",) and _rubric_type(question, c.id) == "tests")
        ]
        total_w = sum(c.weight for c in scored) or 1.0
        score = 100 * sum(c.weight * c.met for c in scored) / total_w
        mistakes: list[str] = []
        words = len(re.findall(r"\w+", answer))
        if words < MIN_WORDS and not code:
            score = min(score, 25.0)
            mistakes.append("Answer is too short to demonstrate understanding; explain your reasoning.")
        if hints_used:
            score = max(0.0, score - HINT_PENALTY * hints_used)
        weak = [c for c in criteria if c.met < 0.5 and c in scored]
        mistakes.extend(f"Missed: {c.description}" for c in weak)
        if code_result and code_result.static_issues:
            mistakes.extend(code_result.static_issues)
        follow_up = follow_up_criterion = None
        texts = {r["id"]: r.get("follow_up") for r in question["rubric"]}
        candidates = [c for c in weak if texts.get(c.id)]
        if candidates and not question.get("is_follow_up"):
            worst = min(candidates, key=lambda c: (c.met, -c.weight))
            follow_up, follow_up_criterion = texts[worst.id], worst.id
        return AnswerEvaluation(
            score=round(score, 1),
            criteria=criteria,
            feedback=self._feedback(question, criteria, scored, code_result),
            mistakes=mistakes,
            missed_concepts=[c.description for c in weak],
            follow_up=follow_up,
            follow_up_criterion=follow_up_criterion,
            code_result=code_result,
            evaluator=evaluator,
        )

    def _refine_with_llm(self, question: dict, answer: str, criteria: list[CriterionResult]) -> bool:
        gradable = [c for c in question["rubric"] if c.get("type") != "tests"]
        if not gradable:
            return False
        rubric = "\n".join(f"- {c['id']} (weight {c['weight']}): {c['description']}" for c in gradable)
        result = self.llm.structured(
            task="interview_grading",
            system=(
                "You grade one interview answer against a fixed rubric. For each criterion return met in [0,1] "
                "and a verbatim quote from the answer that justifies any credit. Be strict and consistent."
                f"\n\nQuestion: {question['prompt']}\n\nRubric:\n{rubric}"
            ),
            content=wrap_untrusted(answer, "answer"),
            schema=LLMRubricJudgement,
            max_tokens=2000,
        )
        if result.value is None:
            return False
        norm_answer = re.sub(r"\s+", " ", answer.lower())
        by_id = {c.id: c for c in criteria}
        used = False
        for item in result.value.criteria:
            c = by_id.get(item.id)
            if c is None or _rubric_type(question, item.id) == "tests":
                continue
            quote = re.sub(r"\s+", " ", (item.quote or "").lower()).strip()
            if item.met > c.met and not (len(quote) >= 8 and quote in norm_answer):
                continue  # ungrounded credit is ignored
            c.met = round(item.met, 3)
            c.evidence = item.quote or c.evidence
            used = True
        return used

    @staticmethod
    def _feedback(
        question: dict,
        criteria: list[CriterionResult],
        scored: list[CriterionResult],
        code_result: CodeExecutionResult | None,
    ) -> str:
        strong = [c.description for c in scored if c.met >= 0.7]
        weak = [c for c in scored if c.met < 0.5]
        parts = []
        if strong:
            parts.append("Strengths: " + "; ".join(strong) + ".")
        if weak:
            hints = {r["id"]: r.get("hint") for r in question["rubric"]}
            parts.append(
                "To improve: "
                + " ".join(f"{c.description}." + (f" ({hints[c.id]})" if hints.get(c.id) else "") for c in weak)
            )
        if code_result and code_result.status == "not_executed":
            parts.append(code_result.detail)
        elif code_result and code_result.status in ("failed", "error", "timeout"):
            parts.append(f"Code: {code_result.detail}".strip())
        return " ".join(parts) or "Solid answer."

    # ------------------------------------------------------------------ summary
    @staticmethod
    def summarize(results: list[dict]) -> dict:
        """results: [{skills, category, score, criteria:[{id,description,met}], is_follow_up}]"""
        per_skill: dict[str, list[float]] = {}
        per_category: dict[str, list[float]] = {}
        misses: dict[str, int] = {}
        for r in results:
            if r.get("is_follow_up"):
                continue
            for s in r["skills"]:
                per_skill.setdefault(s, []).append(r["score"])
            per_category.setdefault(r["category"], []).append(r["score"])
            for c in r["criteria"]:
                if c["met"] < 0.5:
                    misses[c["description"]] = misses.get(c["description"], 0) + 1
        competencies = {s: round(sum(v) / len(v), 1) for s, v in per_skill.items()}
        categories = {k: round(sum(v) / len(v), 1) for k, v in per_category.items()}
        scores = [r["score"] for r in results if not r.get("is_follow_up")]
        overall = round(sum(scores) / len(scores), 1) if scores else 0.0
        recurring = [d for d, n in sorted(misses.items(), key=lambda kv: -kv[1]) if n >= 2]
        weak_skills = sorted((s for s, v in competencies.items() if v < 60), key=lambda s: competencies[s])
        recommendations = [f"Targeted practice on {s} (scored {competencies[s]:.0f}/100)." for s in weak_skills[:4]]
        if recurring:
            recommendations.append("Recurring gap: " + recurring[0] + ".")
        if not recommendations:
            recommendations.append("Strong session. Increase difficulty or try a different category next time.")
        return {
            "overall": overall,
            "competencies": competencies,
            "categories": categories,
            "recurring_weaknesses": recurring[:5],
            "weak_skills": weak_skills,
            "recommendations": recommendations,
        }


def _rubric_type(question: dict, criterion_id: str) -> str | None:
    return next((r.get("type") for r in question["rubric"] if r["id"] == criterion_id), None)
