"""Target-role templates.

A template describes skills commonly required for a role. It is used to (a) resolve a free-text
target role, (b) fill in demand when too few real job listings are available, and (c) choose
interview tracks. Template-derived demand is always labeled as such, never presented as a job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RoleTemplate:
    key: str
    title: str
    aliases: tuple[str, ...]
    required: tuple[str, ...]
    preferred: tuple[str, ...]
    interview_tracks: tuple[str, ...]


ROLES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "backend_engineer",
        "Backend Engineer",
        (
            "backend engineer",
            "backend developer",
            "back-end engineer",
            "back end developer",
            "server-side engineer",
            "api engineer",
            "python backend engineer",
            "software engineer backend",
        ),
        (
            "Python",
            "REST APIs",
            "SQL",
            "PostgreSQL",
            "Data Structures & Algorithms",
            "System Design",
            "Git",
            "Testing",
            "Docker",
        ),
        (
            "FastAPI",
            "Django",
            "Redis",
            "Message Queues",
            "AWS",
            "Kubernetes",
            "CI/CD",
            "Microservices",
            "Caching",
            "Authentication & Security",
            "Observability",
            "Communication",
        ),
        ("dsa", "backend", "system_design", "python", "behavioral"),
    ),
    RoleTemplate(
        "python_developer",
        "Python Developer",
        ("python developer", "python engineer", "python programmer"),
        ("Python", "Object-Oriented Design", "Testing", "Git", "SQL", "Data Structures & Algorithms"),
        ("Django", "FastAPI", "Flask", "Asynchronous Programming", "Docker", "Linux", "REST APIs"),
        ("python", "dsa", "backend", "behavioral"),
    ),
    RoleTemplate(
        "full_stack_engineer",
        "Full Stack Engineer",
        (
            "full stack engineer",
            "full-stack engineer",
            "full stack developer",
            "fullstack developer",
            "full-stack developer",
        ),
        ("JavaScript", "TypeScript", "React", "REST APIs", "SQL", "Git", "HTML/CSS", "Data Structures & Algorithms"),
        ("Node.js", "Next.js", "Python", "PostgreSQL", "Docker", "AWS", "Testing", "System Design", "Tailwind CSS"),
        ("dsa", "backend", "system_design", "behavioral"),
    ),
    RoleTemplate(
        "frontend_engineer",
        "Frontend Engineer",
        ("frontend engineer", "front-end engineer", "frontend developer", "front end developer", "ui engineer"),
        ("JavaScript", "TypeScript", "React", "HTML/CSS", "Git", "Testing"),
        ("Next.js", "Tailwind CSS", "REST APIs", "GraphQL", "Data Structures & Algorithms", "CI/CD"),
        ("dsa", "behavioral", "project"),
    ),
    RoleTemplate(
        "data_engineer",
        "Data Engineer",
        ("data engineer", "etl developer", "analytics engineer", "big data engineer"),
        ("Python", "SQL", "ETL", "Data Warehousing", "Spark", "Airflow", "Git"),
        ("Kafka", "AWS", "GCP", "Docker", "PostgreSQL", "Data Structures & Algorithms", "Database Design", "Terraform"),
        ("python", "dsa", "system_design", "behavioral"),
    ),
    RoleTemplate(
        "ml_engineer",
        "Machine Learning Engineer",
        ("machine learning engineer", "ml engineer", "ai engineer", "applied scientist", "llm engineer"),
        ("Python", "Machine Learning", "Deep Learning", "SQL", "Statistics", "Data Structures & Algorithms", "Git"),
        ("LLM Applications", "Docker", "AWS", "Spark", "Pandas", "NumPy", "System Design", "Vector Databases"),
        ("python", "dsa", "system_design", "behavioral"),
    ),
    RoleTemplate(
        "devops_engineer",
        "DevOps / Site Reliability Engineer",
        (
            "devops engineer",
            "site reliability engineer",
            "sre",
            "platform engineer",
            "infrastructure engineer",
            "cloud engineer",
        ),
        ("Linux", "Docker", "Kubernetes", "CI/CD", "AWS", "Terraform", "Bash", "Observability"),
        ("Python", "Go", "Networking", "GCP", "Azure", "System Design", "Git"),
        ("system_design", "backend", "behavioral"),
    ),
)

BY_KEY = {r.key: r for r in ROLES}


def resolve_role(text: str | None) -> RoleTemplate | None:
    """Best-effort mapping of a free-text target role to a template (longest alias match)."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.lower())
    best: tuple[int, RoleTemplate] | None = None
    for role in ROLES:
        for alias in (*role.aliases, role.title.lower()):
            if alias in t and (best is None or len(alias) > best[0]):
                best = (len(alias), role)
    if best:
        return best[1]
    # Token fallback: "Senior Python Backend" -> backend_engineer.
    for key, words in (
        ("backend_engineer", ("backend", "back-end", "api")),
        ("frontend_engineer", ("frontend", "front-end")),
        ("data_engineer", ("data engineer", "etl")),
        ("ml_engineer", ("machine learning", " ml ", " ai ")),
        ("devops_engineer", ("devops", "sre", "platform")),
        ("python_developer", ("python",)),
    ):
        if any(w in f" {t} " for w in words):
            return BY_KEY[key]
    return None
