"""Resume Analysis Agent.

Produces a validated `ResumeAnalysis` from resume text:
- deterministic, section-aware extraction (skills via taxonomy, experience via date ranges, ...)
- every skill carries verbatim evidence and an evidence level (demonstrated / listed / inferred)
- inferred skills are kept separate from stated ones
- missing information and unclear claims are reported, never filled in
- optional LLM enrichment: anything the LLM adds must quote the resume verbatim, otherwise it is dropped
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.models.enums import EvidenceLevel, SkillSource
from app.schemas.agents import (
    CertificationItem,
    ClaimIssue,
    EducationItem,
    LLMResumeExtraction,
    ProjectItem,
    ResumeAnalysis,
    ResumeSuggestion,
    ResumeTailoring,
    SkillEvidence,
    SkillRequirement,
    WorkExperienceItem,
)
from app.services import skills as tax
from app.services.llm import LLMProvider
from app.services.untrusted import detect_injection, wrap_untrusted

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": (
        "summary",
        "professional summary",
        "profile",
        "about",
        "about me",
        "objective",
        "career objective",
        "overview",
    ),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "career history",
        "relevant experience",
    ),
    "education": ("education", "academic background", "education and training", "qualifications"),
    "skills": (
        "skills",
        "technical skills",
        "core competencies",
        "competencies",
        "technologies",
        "tech stack",
        "tools",
        "skills & tools",
        "skills and tools",
        "key skills",
    ),
    "projects": ("projects", "personal projects", "selected projects", "side projects", "key projects", "open source"),
    "certifications": (
        "certifications",
        "certificates",
        "licenses",
        "licenses & certifications",
        "licenses and certifications",
    ),
    "achievements": ("achievements", "awards", "honors", "honours", "accomplishments", "awards & honors"),
}
_SECTION_LOOKUP = {alias: key for key, aliases in SECTION_ALIASES.items() for alias in aliases}

MONTHS = {
    m: i
    for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)
}
_MONTH_RE = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_RE = rf"(?:{_MONTH_RE}\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
DATE_RANGE = re.compile(
    rf"(?P<start>{_DATE_RE})\s*(?:-|–|—|to|until)\s*(?P<end>{_DATE_RE}|present|current|now|today)", re.I
)
TITLE_WORDS = re.compile(
    r"\b(engineer|developer|programmer|manager|analyst|intern|lead|architect|scientist|consultant|designer|"
    r"specialist|administrator|director|head|officer|associate|sde|swe|devops|sre|researcher|technician|tester|qa)\b",
    re.I,
)
DEGREE = re.compile(
    r"\b(b\.?\s?s\.?c?|b\.?\s?a\.?|bachelor'?s?|b\.?\s?tech|b\.?\s?e\.?|m\.?\s?s\.?c?|master'?s?|m\.?\s?tech|m\.?\s?e\.?|"
    r"mba|ph\.?\s?d|doctorate|associate'?s? degree|diploma|bca|mca)\b",
    re.I,
)
INSTITUTION = re.compile(r"\b(university|college|institute|school|academy|polytechnic|iit|nit)\b", re.I)
YEAR = re.compile(r"\b(19[6-9]\d|20[0-4]\d)\b")
URL = re.compile(r"https?://[^\s)>\]]+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?\d[\d\s().-]{8,}\d)")
LOCATION = re.compile(r"\b([A-Z][a-zA-Z .'-]{1,30}),\s*([A-Z]{2}|[A-Z][a-zA-Z ]{2,30})\b")
BULLET = re.compile(r"^\s*(?:[-•*▪●◦‣·–]|\d+[.)])\s+")
QUANTIFIED = re.compile(
    r"(\d+(?:\.\d+)?\s?%|\$\s?\d|\b\d+(?:\.\d+)?\s?(?:x|k|m|ms|users|customers|requests|hours|days|weeks)\b|\b\d{2,}\b)",
    re.I,
)
IMPACT_VERB = re.compile(
    r"\b(increas|reduc|improv|cut|grew|boost|sav|accelerat|decreas|optimi[sz]|scal|launch|deliver)\w*", re.I
)
VAGUE_CLAIMS = re.compile(r"\b(expert|guru|ninja|rockstar|world[- ]class|master of|best[- ]in[- ]class|10x)\b", re.I)

EVIDENCE_WEIGHT = {
    EvidenceLevel.assessed: 1.0,
    EvidenceLevel.demonstrated: 0.9,
    EvidenceLevel.user_asserted: 0.7,
    EvidenceLevel.listed: 0.55,
    EvidenceLevel.inferred: 0.35,
}


@dataclass
class _Line:
    idx: int
    text: str
    section: str
    start: int  # char offset in normalized text


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _section_of(line: str) -> str | None:
    s = line.strip().strip(":").strip()
    if not s or len(s) > 40:
        return None
    key = re.sub(r"[^a-z& ]", "", s.lower()).strip()
    return _SECTION_LOOKUP.get(key)


def _parse_date(s: str, *, end: bool = False) -> date | None:
    s = s.strip().lower().rstrip(".")
    if s in {"present", "current", "now", "today"}:
        return None
    m = re.match(rf"({_MONTH_RE})\.?\s+(\d{{4}})", s, re.I)
    if m:
        return date(int(m.group(2)), MONTHS[m.group(1)[:3].lower()], 1)
    m = re.match(r"(\d{1,2})/(\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.match(r"(\d{4})", s)
    if m:
        return date(int(m.group(1)), 12 if end else 1, 1)
    return None


def _months_between(a: date, b: date) -> int:
    return max(0, (b.year - a.year) * 12 + (b.month - a.month) + 1)


def total_years(experiences: list[WorkExperienceItem], today: date | None = None) -> float | None:
    """Total experience with overlapping periods merged."""
    today = today or date.today()
    spans = sorted(
        (e.start_date, e.end_date or today) for e in experiences if e.start_date and (e.end_date or e.is_current)
    )
    if not spans:
        return None
    merged: list[list[date]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    months = sum(_months_between(s, e) for s, e in merged)
    return round(months / 12, 1)


class ResumeAnalysisAgent:
    name = "resume_analysis"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    # ------------------------------------------------------------------ public
    def analyze(self, text: str, *, today: date | None = None) -> ResumeAnalysis:
        flags = detect_injection(text)
        lines = self._split(text)
        flagged = self._flagged_lines(lines, flags)
        usable = [ln for ln in lines if ln.idx not in flagged]

        experiences = self._experiences([ln for ln in usable if ln.section == "experience"])
        projects = self._projects([ln for ln in usable if ln.section == "projects"])
        education = self._education([ln for ln in usable if ln.section == "education"])
        certifications = self._certifications(usable)
        skills = self._skills(usable, experiences, today)
        achievements = self._achievements(usable)
        years = total_years(experiences, today)
        name, location = self._contact(lines)

        analysis = ResumeAnalysis(
            candidate_name=name,
            location=location,
            headline=experiences[0].title if experiences and experiences[0].title else None,
            summary=self._summary(usable, skills, years),
            skills=skills,
            experiences=experiences,
            projects=projects,
            education=education,
            certifications=certifications,
            achievements=achievements,
            total_years_experience=years,
            security_flags=flags,
        )
        if flags:
            analysis.warnings.append(
                f"{len(flags)} instruction-like passage(s) were found in the resume and excluded from extraction."
            )
        self._enrich_with_llm(analysis, text, flagged_text=[ln.text for ln in lines if ln.idx in flagged])
        analysis.missing_information = self._missing_info(analysis, text)
        analysis.unclear_claims = self._unclear_claims(analysis, usable)
        return ResumeAnalysis.model_validate(analysis.model_dump())

    def tailor(self, analysis: ResumeAnalysis, job_title: str, requirements: list[SkillRequirement]) -> ResumeTailoring:
        """Suggest resume improvements for one job. Never suggests claiming skills without evidence."""
        by_skill = {s.skill: s for s in analysis.skills}
        matched, missing_req, missing_pref, suggestions = [], [], [], []
        for req in requirements:
            have = by_skill.get(req.skill)
            if have is None:
                (missing_req if req.level == "required" else missing_pref).append(req.skill)
                suggestions.append(
                    ResumeSuggestion(
                        kind="keyword_gap",
                        section="Skills / Experience",
                        skill=req.skill,
                        suggestion=(
                            f"The role {'requires' if req.level == 'required' else 'prefers'} {req.skill}. "
                            f"If you have used it, add a concrete example (what you built, scale, outcome). "
                            f"If not, treat it as a learning gap rather than adding the keyword."
                        ),
                        rationale="No evidence of this skill was found in your resume.",
                    )
                )
                continue
            matched.append(req.skill)
            if have.evidence_level == EvidenceLevel.listed:
                suggestions.append(
                    ResumeSuggestion(
                        kind="add_evidence",
                        section="Experience",
                        skill=req.skill,
                        suggestion=f"{req.skill} only appears in your skills list. "
                        "Add a bullet that shows where you used it.",
                        rationale="Recruiters weigh demonstrated use more than a listed keyword.",
                    )
                )
            elif have.evidence_level == EvidenceLevel.inferred:
                suggestions.append(
                    ResumeSuggestion(
                        kind="clarify",
                        section="Skills",
                        skill=req.skill,
                        suggestion=(
                            f"Your resume implies {req.skill} (via {have.inferred_from}). "
                            f"If you used it directly, state it explicitly with an example."
                        ),
                        rationale="Inferred skills are not visible to screeners.",
                    )
                )
        unquantified = [h for e in analysis.experiences for h in e.highlights if not QUANTIFIED.search(h)]
        for h in unquantified[:3]:
            suggestions.append(
                ResumeSuggestion(
                    kind="quantify",
                    section="Experience",
                    suggestion=f'Add a measurable outcome to: "{h[:140]}"',
                    rationale="Quantified impact (latency, users, revenue, time saved) makes claims verifiable.",
                )
            )
        for c in analysis.unclear_claims[:3]:
            suggestions.append(
                ResumeSuggestion(
                    kind="remove_unsupported",
                    section="Summary",
                    suggestion=f'Support or rephrase: "{c.claim[:140]}"',
                    rationale=c.reason,
                )
            )
        return ResumeTailoring(
            job_title=job_title,
            matched_keywords=matched,
            missing_required=missing_req,
            missing_preferred=missing_pref,
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------ parsing
    def _split(self, text: str) -> list[_Line]:
        out: list[_Line] = []
        section = "header"
        offset = 0
        for i, raw in enumerate(text.splitlines()):
            line = raw.strip()
            sec = _section_of(line)
            if sec:
                section = sec
            elif line:
                out.append(_Line(i, line, section, offset))
            offset += len(raw) + 1
        return out

    def _flagged_lines(self, lines: list[_Line], flags: list[dict]) -> set[int]:
        if not flags:
            return set()
        flagged = set()
        for ln in lines:
            if detect_injection(ln.text):
                flagged.add(ln.idx)
        return flagged

    def _contact(self, lines: list[_Line]) -> tuple[str | None, str | None]:
        header = [ln.text for ln in lines[:6]]
        name = None
        for t in header[:2]:
            words = t.split()
            if (
                2 <= len(words) <= 4
                and all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", w) for w in words)
                and not tax.find_skills(t)
                and not TITLE_WORDS.search(t)
            ):
                name = t
                break
        location = None
        for t in header:
            cleaned = EMAIL.sub(" ", URL.sub(" ", t))
            m = LOCATION.search(cleaned)
            if m and not TITLE_WORDS.search(m.group(0)):
                location = m.group(0).strip()
                break
        return name, location

    def _experiences(self, lines: list[_Line]) -> list[WorkExperienceItem]:
        entries: list[WorkExperienceItem] = []
        pending_header: list[str] = []
        current: WorkExperienceItem | None = None
        for ln in lines:
            m = DATE_RANGE.search(ln.text)
            is_bullet = bool(BULLET.match(ln.text))
            if m and not is_bullet:
                header = (ln.text[: m.start()] + " " + ln.text[m.end() :]).strip(" |,–—-()\t")
                parts = [p for p in re.split(r"\s*(?:\||—|–| - | at |,|@)\s*", header) if p.strip()]
                if not parts and pending_header:
                    parts = [p for h in pending_header[-2:] for p in re.split(r"\s*(?:\||—|–| - | at |,|@)\s*", h) if p]
                elif len(parts) == 1 and pending_header:
                    parts = [*parts, *[p for p in re.split(r"\s*(?:\||—|–| - | at |,|@)\s*", pending_header[-1]) if p]]
                title, company, location = self._title_company(parts)
                start = _parse_date(m.group("start"))
                end_raw = m.group("end")
                end = _parse_date(end_raw, end=True)
                current = WorkExperienceItem(
                    title=title,
                    company=company,
                    location=location,
                    start_date=start,
                    end_date=end,
                    is_current=end_raw.lower() in {"present", "current", "now", "today"},
                )
                entries.append(current)
                pending_header = []
            elif current is not None and (is_bullet or len(ln.text) > 60):
                current.highlights.append(BULLET.sub("", ln.text).strip())
            else:
                pending_header.append(ln.text)
                if current is not None and len(pending_header) > 2:
                    current.highlights.extend(pending_header[:-2])
                    pending_header = pending_header[-2:]
        for e in entries:
            e.skills = sorted({m.skill for h in e.highlights for m in tax.find_skills(h)})
        return entries

    @staticmethod
    def _title_company(parts: list[str]) -> tuple[str, str, str]:
        parts = [p.strip() for p in parts if p.strip()]
        title = next((p for p in parts if TITLE_WORDS.search(p)), "")
        rest = [p for p in parts if p != title]
        company = rest[0] if rest else ""
        location = rest[1] if len(rest) > 1 and len(rest[1]) < 40 else ""
        if not title and parts:
            title = parts[0]
            company = parts[1] if len(parts) > 1 else ""
        return title[:200], company[:200], location[:120]

    def _projects(self, lines: list[_Line]) -> list[ProjectItem]:
        projects: list[ProjectItem] = []
        for ln in lines:
            if BULLET.match(ln.text) and projects:
                projects[-1].highlights.append(BULLET.sub("", ln.text))
                continue
            if not projects or len(ln.text) <= 90 or re.search(r"\s[|—–:]\s?", ln.text[:80]):
                head = re.split(r"\s*(?:\||—|–|:)\s*", ln.text, maxsplit=1)
                url = URL.search(ln.text)
                projects.append(
                    ProjectItem(
                        name=head[0][:200],
                        description=head[1] if len(head) > 1 else "",
                        url=url.group(0) if url else None,
                    )
                )
            else:
                projects[-1].highlights.append(ln.text)
        for p in projects:
            blob = " ".join([p.name, p.description, *p.highlights])
            p.technologies = sorted({m.skill for m in tax.find_skills(blob)})
        return projects

    def _education(self, lines: list[_Line]) -> list[EducationItem]:
        items: list[EducationItem] = []
        for ln in lines:
            deg = DEGREE.search(ln.text)
            inst = INSTITUTION.search(ln.text)
            years = YEAR.findall(ln.text)
            if not (deg or inst):
                if items and years and items[-1].graduation_year is None:
                    items[-1].graduation_year = int(years[-1])
                continue
            parts = [p.strip() for p in re.split(r"\s*(?:\||—|–|,| - )\s*", ln.text) if p.strip()]
            institution = next((p for p in parts if INSTITUTION.search(p)), "")
            degree_part = next((p for p in parts if DEGREE.search(p)), "")
            if items and not items[-1].institution and institution and not degree_part:
                items[-1].institution = institution
                continue
            if items and not items[-1].degree and degree_part and not institution:
                items[-1].degree = degree_part
                continue
            fld = ""
            fm = re.search(r"\b(?:in|of)\s+([A-Z][A-Za-z &]+)", degree_part)
            if fm:
                fld = fm.group(1).strip()
            items.append(
                EducationItem(
                    institution=institution,
                    degree=degree_part,
                    field=fld,
                    graduation_year=int(years[-1]) if years else None,
                    raw=ln.text[:300],
                )
            )
        return items

    def _certifications(self, lines: list[_Line]) -> list[CertificationItem]:
        certs: list[CertificationItem] = []
        for ln in lines:
            if ln.section == "certifications" or (
                ln.section not in {"experience", "projects"} and re.search(r"\bcertifi(ed|cation)\b", ln.text, re.I)
            ):
                name = BULLET.sub("", ln.text)
                years = YEAR.findall(name)
                parts = [p.strip() for p in re.split(r"\s*(?:\||—|–|,| - )\s*", name) if p.strip()]
                if not parts:
                    continue
                certs.append(
                    CertificationItem(
                        name=parts[0][:200],
                        issuer=parts[1][:120] if len(parts) > 1 and not YEAR.fullmatch(parts[1]) else "",
                        year=int(years[-1]) if years else None,
                    )
                )
        return certs

    def _skills(
        self, lines: list[_Line], experiences: list[WorkExperienceItem], today: date | None
    ) -> list[SkillEvidence]:
        agg: dict[str, dict] = {}
        for ln in lines:
            for m in tax.find_skills(ln.text):
                level = EvidenceLevel.demonstrated if ln.section in {"experience", "projects"} else EvidenceLevel.listed
                entry = agg.setdefault(m.skill, {"levels": set(), "evidence": [], "count": 0})
                entry["levels"].add(level)
                entry["count"] += 1
                snippet = BULLET.sub("", ln.text)[:220]
                if snippet not in entry["evidence"] and len(entry["evidence"]) < 4:
                    entry["evidence"].append(snippet)

        today = today or date.today()
        out: list[SkillEvidence] = []
        for skill, e in agg.items():
            level = EvidenceLevel.demonstrated if EvidenceLevel.demonstrated in e["levels"] else EvidenceLevel.listed
            base = 0.85 if level == EvidenceLevel.demonstrated else 0.6
            years = None
            used_in = [x for x in experiences if skill in x.skills and x.start_date]
            if used_in:
                years = total_years(used_in, today)
            out.append(
                SkillEvidence(
                    skill=skill,
                    category=tax.category_of(skill),
                    source=SkillSource.explicit,
                    evidence_level=level,
                    evidence=e["evidence"],
                    years=years,
                    confidence=round(min(0.95, base + 0.03 * (e["count"] - 1)), 2),
                )
            )
        stated = {s.skill for s in out}
        for s in list(out):
            for implied in tax.implied_skills(s.skill):
                if implied not in stated:
                    stated.add(implied)
                    out.append(
                        SkillEvidence(
                            skill=implied,
                            category=tax.category_of(implied),
                            source=SkillSource.inferred,
                            evidence_level=EvidenceLevel.inferred,
                            evidence=[f"Implied by {s.skill}"],
                            confidence=0.4,
                            inferred_from=s.skill,
                        )
                    )
        out.sort(key=lambda s: (s.source != SkillSource.explicit, -EVIDENCE_WEIGHT[s.evidence_level], s.skill))
        return out

    def _achievements(self, lines: list[_Line]) -> list[str]:
        out = []
        for ln in lines:
            t = BULLET.sub("", ln.text)
            if ln.section == "achievements" or (
                ln.section in {"experience", "projects"}
                and QUANTIFIED.search(t)
                and IMPACT_VERB.search(t)
                and not DATE_RANGE.search(t)
            ):
                out.append(t[:300])
        return out[:15]

    def _summary(self, lines: list[_Line], skills: list[SkillEvidence], years: float | None) -> str:
        own = " ".join(ln.text for ln in lines if ln.section == "summary")
        if own:
            return own[:800]
        top = [s.skill for s in skills if s.evidence_level == EvidenceLevel.demonstrated][:5]
        bits = []
        if years is not None:
            bits.append(f"About {years:g} years of dated work experience")
        if top:
            bits.append("strongest demonstrated skills: " + ", ".join(top))
        return ("; ".join(bits) + ".") if bits else ""

    def _missing_info(self, a: ResumeAnalysis, text: str) -> list[str]:
        missing = []
        if not EMAIL.search(text):
            missing.append("No contact email found.")
        if not a.experiences:
            missing.append("No work experience entries with recognizable dates were found.")
        elif any(e.start_date is None for e in a.experiences):
            missing.append("Some experience entries have no start date.")
        if any(not e.company for e in a.experiences):
            missing.append("Some experience entries have no identifiable company name.")
        if not a.education:
            missing.append("No education section was found.")
        if not a.achievements:
            missing.append("No quantified achievements (numbers, percentages, scale) were found.")
        if not a.projects and (a.total_years_experience or 0) < 3:
            missing.append("No projects section; projects are valuable evidence for early-career candidates.")
        if not any(s.evidence_level == EvidenceLevel.demonstrated for s in a.skills):
            missing.append("Skills are listed but none are tied to described work or projects.")
        return missing

    def _unclear_claims(self, a: ResumeAnalysis, lines: list[_Line]) -> list[ClaimIssue]:
        issues = []
        for ln in lines:
            m = VAGUE_CLAIMS.search(ln.text)
            if m:
                issues.append(
                    ClaimIssue(
                        claim=ln.text[:200], reason=f'"{m.group(0)}" is a self-assessment without supporting evidence.'
                    )
                )
        listed_only = [s.skill for s in a.skills if s.evidence_level == EvidenceLevel.listed]
        if listed_only:
            issues.append(
                ClaimIssue(
                    claim="Listed skills: " + ", ".join(listed_only[:12]),
                    reason="These appear only in a skills list with no described usage, "
                    "so proficiency cannot be judged.",
                )
            )
        return issues[:10]

    # ------------------------------------------------------------------ LLM enrichment (grounded)
    def _enrich_with_llm(self, analysis: ResumeAnalysis, text: str, flagged_text: list[str]) -> None:
        result = self.llm.structured(
            task="resume_extraction",
            system=(
                "You extract structured facts from a resume. Only include skills, roles and achievements that the "
                "resume explicitly states, each with a verbatim quote. Do not infer or embellish."
            ),
            content=wrap_untrusted(text, "resume"),
            schema=LLMResumeExtraction,
        )
        if result.value is None:
            if result.degraded:
                analysis.warnings.append(f"LLM enrichment unavailable ({result.error}); used deterministic extraction.")
            return
        source = _norm(text)
        flagged = [_norm(t) for t in flagged_text]
        by_skill = {s.skill: s for s in analysis.skills}
        dropped = 0

        def grounded(quote: str) -> bool:
            q = _norm(quote)
            return len(q) >= 3 and q in source and not any(q in f or f in q for f in flagged if f)

        for item in result.value.skills:
            canonical = tax.normalize_skill(item.name)
            if not canonical or not grounded(item.quote):
                dropped += 1
                continue
            # The quote must actually mention the skill (any alias).
            if not any(m.skill == canonical for m in tax.find_skills(item.quote, require_context_for_ambiguous=False)):
                dropped += 1
                continue
            existing = by_skill.get(canonical)
            if existing is None:
                new = SkillEvidence(
                    skill=canonical,
                    category=tax.category_of(canonical),
                    source=SkillSource.explicit,
                    evidence_level=EvidenceLevel.listed,
                    evidence=[item.quote[:220]],
                    confidence=0.6,
                )
                analysis.skills.append(new)
                by_skill[canonical] = new
            elif existing.source == SkillSource.inferred:
                existing.source, existing.evidence_level = SkillSource.explicit, EvidenceLevel.listed
                existing.evidence, existing.inferred_from, existing.confidence = [item.quote[:220]], None, 0.6
        for ach in result.value.achievements:
            if grounded(ach) and ach not in analysis.achievements:
                analysis.achievements.append(ach[:300])
            elif not grounded(ach):
                dropped += 1
        mentioned = {m.skill for m in tax.find_skills(result.value.summary)}
        if result.value.summary and mentioned <= set(by_skill):
            analysis.summary = result.value.summary[:800]
        analysis.extraction_method = "llm_grounded"
        if dropped:
            analysis.warnings.append(
                f"{dropped} LLM-extracted item(s) were discarded because they were not supported "
                f"verbatim by the resume."
            )
