"""Deterministic extraction of structured requirements from job descriptions."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime

from app.models.enums import RequirementLevel, Seniority, WorkArrangement
from app.schemas.agents import NormalizedJob, SkillRequirement
from app.services import skills as tax
from app.services.untrusted import clean_text, detect_injection

REQUIRED_HEADERS = re.compile(
    r"^(requirements|qualifications|minimum qualifications|basic qualifications|required qualifications|"
    r"what you'?ll need|what we'?re looking for|must[- ]haves?|you have|you bring|about you|who you are|"
    r"skills required|required skills|your profile)\b",
    re.I,
)
PREFERRED_HEADERS = re.compile(
    r"^(nice[- ]to[- ]haves?|preferred( qualifications)?|bonus( points)?|pluses|good to have|extra credit|"
    r"it'?s a plus|desired skills|ideally)\b",
    re.I,
)
OTHER_HEADERS = re.compile(
    r"^(responsibilities|what you'?ll do|the role|about (us|the team|the company)|benefits|perks|compensation|"
    r"our stack|why join|how to apply|equal opportunity)\b",
    re.I,
)
PREFERRED_INLINE = re.compile(r"\b(preferred|nice to have|a plus|bonus|is a plus|ideally|familiarity with)\b", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*years?", re.I)


def strip_html(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/h\d|/ul|/ol|p|h\d)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(text)


def extract_requirements(description: str) -> list[SkillRequirement]:
    section = "other"
    found: dict[str, SkillRequirement] = {}
    for raw in description.splitlines():
        line = raw.strip().lstrip("-•* ").strip()
        if not line:
            continue
        head = line.rstrip(":").strip()
        if len(head) < 60 and REQUIRED_HEADERS.match(head):
            section = "required"
            continue
        if len(head) < 60 and PREFERRED_HEADERS.match(head):
            section = "preferred"
            continue
        if len(head) < 60 and OTHER_HEADERS.match(head):
            section = "other"
            continue
        if detect_injection(line):
            continue  # never take requirements from instruction-like text
        mentions = tax.find_skills(line)
        if not mentions:
            continue
        if section == "preferred" or PREFERRED_INLINE.search(line):
            level = RequirementLevel.preferred
        elif section == "required":
            level = RequirementLevel.required
        else:
            # Skills mentioned in responsibilities/stack are treated as preferred, not mandatory.
            level = RequirementLevel.preferred
        for m in mentions:
            prev = found.get(m.skill)
            if prev is None or (prev.level == RequirementLevel.preferred and level == RequirementLevel.required):
                found[m.skill] = SkillRequirement(skill=m.skill, level=level, evidence_text=line[:300])
    return sorted(found.values(), key=lambda r: (r.level != RequirementLevel.required, r.skill))


def extract_min_years(description: str) -> float | None:
    values = []
    for m in YEARS.finditer(description):
        window = description[m.end() : m.end() + 60].lower()
        if "experience" in window or "of " in window[:4]:
            n = int(m.group(1))
            if 0 < n <= 20:
                values.append(n)
    return float(min(values)) if values else None


def infer_seniority(title: str, min_years: float | None) -> Seniority:
    t = title.lower()
    for words, level in (
        (("intern", "internship", "trainee"), Seniority.intern),
        (("principal", "staff", "distinguished"), Seniority.staff),
        (("lead", "manager", "head of"), Seniority.lead),
        (("senior", "sr.", "sr "), Seniority.senior),
        (("junior", "jr.", "jr ", "entry", "graduate", "associate"), Seniority.junior),
        ((" ii", " 2", "mid", "intermediate"), Seniority.mid),
    ):
        if any(w in f" {t} " for w in words):
            return level
    if min_years is None:
        return Seniority.unknown
    if min_years < 2:
        return Seniority.junior
    if min_years < 5:
        return Seniority.mid
    return Seniority.senior


def infer_arrangement(location: str, description: str, hint: str | None = None) -> WorkArrangement:
    blob = f"{hint or ''} {location}".lower()
    if "hybrid" in blob:
        return WorkArrangement.hybrid
    if "remote" in blob:
        return WorkArrangement.remote
    if hint and hint.lower() in {"on-site", "onsite", "in-office"}:
        return WorkArrangement.onsite
    d = description.lower()
    if re.search(r"\bhybrid\b", d):
        return WorkArrangement.hybrid
    if re.search(r"\b(fully remote|remote[- ]first|100% remote|work from anywhere)\b", d):
        return WorkArrangement.remote
    return WorkArrangement.onsite if location.strip() else WorkArrangement.unknown


def infer_employment_type(text: str) -> str:
    t = text.lower()
    for key, words in (
        ("internship", ("intern",)),
        ("contract", ("contract", "contractor", "freelance")),
        ("part_time", ("part-time", "part time")),
        ("full_time", ("full-time", "full time", "permanent")),
    ):
        if any(w in t for w in words):
            return key
    return "unknown"


def fingerprint(company: str, title: str, location: str) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    title_n = re.sub(r"\b(sr|senior|jr|junior)\b", lambda m: m.group(0)[:2], norm(title))
    return hashlib.sha256(f"{norm(company)}|{title_n}|{norm(location)}".encode()).hexdigest()


def quality_flags(job: NormalizedJob, *, stale_after_days: int, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    flags: list[str] = []
    if job.is_demo:
        flags.append("demo_data")
    # For live feeds, presence in today's feed is the freshness signal; "stale" means not re-seen in the
    # feed recently (see stale_flags). A long-open posting is only noted. Demo data uses its posting date.
    seen = job.posted_at if job.is_demo else job.retrieved_at
    if job.posted_at is None:
        flags.append("no_posting_date")
    elif not job.is_demo and (now - job.posted_at).days > 2 * stale_after_days:
        flags.append("long_open")
    if seen is not None and (now - seen).days > stale_after_days:
        flags.append("stale")
    if not job.location:
        flags.append("missing_location")
    if not job.requirements:
        flags.append("no_requirements_extracted")
    if len(job.description) < 200:
        flags.append("short_description")
    if not job.application_url:
        flags.append("no_application_url")
    if job.is_demo or not job.application_url:
        flags.append("unverifiable")
    if detect_injection(job.description):
        flags.append("suspicious_content")
    return flags


def dedupe(jobs: list[NormalizedJob]) -> list[NormalizedJob]:
    """Drop duplicates by (source, external_id) and by cross-source fingerprint.
    Prefers non-demo listings, then the most recently posted."""
    best: dict[str, NormalizedJob] = {}
    seen_ids: set[tuple[str, str]] = set()
    for job in jobs:
        key_id = (job.source, job.external_id)
        if key_id in seen_ids:
            continue
        seen_ids.add(key_id)
        fp = fingerprint(job.company, job.title, job.location)
        cur = best.get(fp)
        if cur is None:
            best[fp] = job
            continue
        rank = lambda j: (not j.is_demo, j.posted_at or datetime.min.replace(tzinfo=UTC))  # noqa: E731
        if rank(job) > rank(cur):
            best[fp] = job
    return list(best.values())


def stale_flags(
    flags: list[str],
    last_checked_at: datetime | None,
    is_demo: bool,
    stale_after_days: int,
    now: datetime | None = None,
) -> list[str]:
    """Flags as of now: a real listing not re-seen in its feed for `stale_after_days` becomes stale."""
    if is_demo or last_checked_at is None or "stale" in flags:
        return list(flags)
    checked = last_checked_at if last_checked_at.tzinfo else last_checked_at.replace(tzinfo=UTC)
    if ((now or datetime.now(UTC)) - checked).days > stale_after_days:
        return [*flags, "stale"]
    return list(flags)


UNRESTRICTED_REMOTE = {"", "anywhere", "global", "worldwide", "work from home", "wfh", "remote first", "fully"}
# Lets "Bengaluru" also accept "Remote - India" when the user did not list the country.
CITY_COUNTRY = {
    **dict.fromkeys(
        [
            "bengaluru",
            "bangalore",
            "hyderabad",
            "pune",
            "mumbai",
            "chennai",
            "delhi",
            "new delhi",
            "noida",
            "gurugram",
            "gurgaon",
            "kolkata",
            "ahmedabad",
            "kochi",
            "jaipur",
            "indore",
            "chandigarh",
        ],
        "india",
    ),
    **dict.fromkeys(
        ["new york", "san francisco", "seattle", "austin", "boston", "chicago", "los angeles"], "united states"
    ),
    **dict.fromkeys(["london", "manchester", "edinburgh"], "united kingdom"),
    **dict.fromkeys(["toronto", "vancouver", "montreal"], "canada"),
    **dict.fromkeys(["berlin", "munich", "hamburg"], "germany"),
    "singapore": "singapore",
    "dublin": "ireland",
    "amsterdam": "netherlands",
    "sydney": "australia",
}
COUNTRY_ALIASES = {
    "united states": ["usa", "us", "united states", "america"],
    "united kingdom": ["uk", "united kingdom"],
}


def location_fits(location: str, preferred: list[str]) -> bool:
    """Does a listing's location suit the candidate's preferred locations?

    Remote roles are often limited to a country ("Remote - USA"), so remote only fits when it is
    unrestricted or its stated region matches a preferred location (e.g. "Remote - India").
    """
    if not preferred:
        return True
    loc = (location or "").lower()
    prefs = [p.lower().strip() for p in preferred if p.strip()]
    if not loc or any(p in loc or loc in p for p in prefs):
        return True
    if "remote" not in loc:
        return False
    countries = {CITY_COUNTRY[p] for p in prefs if p in CITY_COUNTRY} | {p for p in prefs if p not in CITY_COUNTRY}
    accepted = {alias for c in countries for alias in COUNTRY_ALIASES.get(c, [c])}
    regions = [re.sub(r"[^a-z ]+", " ", part.replace("remote", "")).strip() for part in re.split(r"[;/|]", loc)]
    regions = [re.sub(r"\s+", " ", r) for r in regions]
    return any(r in UNRESTRICTED_REMOTE or any(re.search(rf"\b{re.escape(a)}\b", r) for a in accepted) for r in regions)
