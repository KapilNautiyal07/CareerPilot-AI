"""Skill taxonomy and deterministic normalization.

Every agent speaks in canonical skill names from this taxonomy so that resume skills,
job requirements, gaps, roadmap tasks and interview competencies line up exactly.

- aliases:  surface forms matched case-insensitively on word boundaries
- related:  skills that transfer partially (credit for a *partial* match, never a full one)
- implies:  skills that can be *inferred* (e.g. Django implies Python), flagged as inferred
- hours:    rough hours to reach working, interview-ready familiarity from zero
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class SkillDef:
    name: str
    category: str
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    implies: tuple[str, ...] = ()
    hours: float = 20.0
    interview_category: str | None = None  # maps to an interview question category
    tags: tuple[str, ...] = field(default=())


def _s(
    name: str,
    category: str,
    aliases: str = "",
    related: str = "",
    implies: str = "",
    hours: float = 20.0,
    interview: str | None = None,
) -> SkillDef:
    split = lambda v: tuple(x.strip() for x in v.split("|") if x.strip())  # noqa: E731
    return SkillDef(name, category, split(aliases), split(related), split(implies), hours, interview)


TAXONOMY: tuple[SkillDef, ...] = (
    # Languages
    _s("Python", "language", "python|python3|py", "Go|Java", hours=40, interview="python"),
    _s("Java", "language", "java", "Kotlin|C#", hours=45),
    _s("JavaScript", "language", "javascript|js|ecmascript|es6", "TypeScript", hours=35),
    _s("TypeScript", "language", "typescript|ts", "JavaScript", "JavaScript", hours=20),
    _s("Go", "language", "golang|go lang", "Python|Java", hours=35),
    _s("C#", "language", "c#|csharp|c sharp|.net|dotnet", "Java", hours=40),
    _s("C++", "language", "c++|cpp", "C", hours=50),
    _s("C", "language", "c programming|ansi c", "C++", hours=40),
    _s("Kotlin", "language", "kotlin", "Java", hours=30),
    _s("Rust", "language", "rust|rustlang", "C++|Go", hours=50),
    _s("Ruby", "language", "ruby", "Python", hours=30),
    _s("PHP", "language", "php", "", hours=30),
    _s("SQL", "database", "sql|t-sql|pl/sql|plpgsql", "", hours=25),
    _s("Bash", "devops", "bash|shell scripting|shell|zsh", "Linux", hours=12),
    # Backend frameworks & concepts
    _s("FastAPI", "backend", "fastapi|fast api", "Flask|Django", "Python|REST APIs", hours=15, interview="backend"),
    _s(
        "Django",
        "backend",
        "django|django rest framework|drf",
        "Flask|FastAPI",
        "Python|REST APIs",
        hours=25,
        interview="backend",
    ),
    _s("Flask", "backend", "flask", "FastAPI|Django", "Python|REST APIs", hours=15, interview="backend"),
    _s("Node.js", "backend", "node.js|nodejs|node", "Express", "JavaScript", hours=25, interview="backend"),
    _s("Express", "backend", "express|express.js|expressjs", "Node.js|FastAPI", "Node.js|REST APIs", hours=12),
    _s("Spring Boot", "backend", "spring boot|spring|spring framework", "Django", "Java|REST APIs", hours=35),
    _s(
        "REST APIs",
        "backend",
        "rest|restful|rest api|rest apis|restful api|restful apis|api design",
        "GraphQL|gRPC",
        hours=15,
        interview="backend",
    ),
    _s("GraphQL", "backend", "graphql", "REST APIs", hours=12),
    _s("gRPC", "backend", "grpc|protobuf|protocol buffers", "REST APIs", hours=12),
    _s(
        "Microservices",
        "backend",
        "microservices|microservice|service-oriented architecture|soa",
        "System Design|Distributed Systems",
        hours=25,
        interview="system_design",
    ),
    _s(
        "Distributed Systems",
        "backend",
        "distributed systems|distributed computing",
        "Microservices|System Design",
        hours=40,
        interview="system_design",
    ),
    _s(
        "System Design",
        "cs_fundamentals",
        "system design|systems design|scalable systems|scalability|high availability",
        "Distributed Systems|Microservices",
        hours=40,
        interview="system_design",
    ),
    _s(
        "Asynchronous Programming",
        "backend",
        "asyncio|async/await|asynchronous programming|concurrency|multithreading",
        "",
        hours=15,
        interview="python",
    ),
    _s(
        "Authentication & Security",
        "backend",
        "oauth|oauth2|jwt|authentication|authorization|owasp|application security",
        "",
        hours=15,
        interview="backend",
    ),
    _s("Caching", "backend", "caching|cache|memcached", "Redis", hours=10, interview="system_design"),
    _s(
        "Message Queues",
        "backend",
        "message queue|message queues|rabbitmq|celery|sqs|pub/sub|pubsub",
        "Kafka",
        hours=15,
        interview="system_design",
    ),
    _s("Kafka", "backend", "kafka|apache kafka", "Message Queues", "Message Queues", hours=20),
    _s("ORM", "backend", "sqlalchemy|orm|django orm|hibernate|prisma|sequelize", "SQL", "SQL", hours=10),
    # Databases
    _s("PostgreSQL", "database", "postgresql|postgres|psql", "MySQL|SQL", "SQL", hours=20, interview="backend"),
    _s("MySQL", "database", "mysql|mariadb", "PostgreSQL|SQL", "SQL", hours=18),
    _s("MongoDB", "database", "mongodb|mongo", "NoSQL", "NoSQL", hours=15),
    _s("Redis", "database", "redis", "Caching", "Caching", hours=10),
    _s("NoSQL", "database", "nosql|dynamodb|cassandra|couchdb", "MongoDB", hours=15),
    _s(
        "Database Design",
        "database",
        "database design|data modeling|data modelling|schema design|normalization",
        "SQL",
        hours=15,
        interview="backend",
    ),
    _s("Elasticsearch", "database", "elasticsearch|opensearch|elastic search", "", hours=15),
    _s(
        "Vector Databases",
        "database",
        "pgvector|vector database|vector databases|pinecone|weaviate|qdrant|faiss",
        "Elasticsearch",
        hours=10,
    ),
    # Frontend
    _s("React", "frontend", "react|react.js|reactjs", "Vue.js|Angular", "JavaScript", hours=30),
    _s("Next.js", "frontend", "next.js|nextjs", "React", "React", hours=15),
    _s("Vue.js", "frontend", "vue|vue.js|vuejs", "React|Angular", "JavaScript", hours=25),
    _s("Angular", "frontend", "angular|angularjs", "React|Vue.js", "TypeScript", hours=30),
    _s("HTML/CSS", "frontend", "html|css|html5|css3|sass|scss", "Tailwind CSS", hours=15),
    _s("Tailwind CSS", "frontend", "tailwind|tailwindcss|tailwind css", "HTML/CSS", "HTML/CSS", hours=6),
    # Cloud & DevOps
    _s("AWS", "cloud", "aws|amazon web services|ec2|s3|lambda|ecs|eks|rds", "GCP|Azure", hours=40),
    _s("GCP", "cloud", "gcp|google cloud|google cloud platform|bigquery|cloud run", "AWS|Azure", hours=35),
    _s("Azure", "cloud", "azure|microsoft azure", "AWS|GCP", hours=35),
    _s("Docker", "devops", "docker|containers|containerization|dockerfile|docker compose", "Kubernetes", hours=15),
    _s("Kubernetes", "devops", "kubernetes|k8s|helm", "Docker", "Docker", hours=35),
    _s("Terraform", "devops", "terraform|infrastructure as code|iac|pulumi|cloudformation", "", hours=20),
    _s(
        "CI/CD",
        "devops",
        "ci/cd|continuous integration|continuous delivery|continuous deployment|github actions|jenkins"
        "|gitlab ci|circleci",
        "",
        hours=12,
    ),
    _s("Linux", "devops", "linux|unix|ubuntu|debian|centos", "Bash", hours=20),
    _s("Git", "practice", "git|github|gitlab|bitbucket|version control", "", hours=6),
    _s(
        "Observability",
        "devops",
        "observability|monitoring|prometheus|grafana|datadog|opentelemetry|logging|tracing",
        "",
        hours=15,
    ),
    # Data & ML
    _s("Pandas", "data", "pandas", "NumPy", "Python", hours=15),
    _s("NumPy", "data", "numpy", "Pandas", "Python", hours=10),
    _s("Spark", "data", "spark|pyspark|apache spark|databricks", "Pandas", hours=30),
    _s("Airflow", "data", "airflow|apache airflow|dagster|prefect", "", hours=15),
    _s("ETL", "data", "etl|elt|data pipelines|data pipeline|data engineering", "Airflow", hours=20),
    _s("Data Warehousing", "data", "data warehouse|data warehousing|snowflake|redshift|dbt", "SQL", hours=20),
    _s("Machine Learning", "ml", "machine learning|ml|scikit-learn|sklearn|xgboost", "Deep Learning", hours=60),
    _s(
        "Deep Learning",
        "ml",
        "deep learning|pytorch|tensorflow|keras|neural networks",
        "Machine Learning",
        "Machine Learning",
        hours=60,
    ),
    _s(
        "LLM Applications",
        "ml",
        "llm|llms|large language models|langchain|langgraph|rag|retrieval-augmented generation"
        "|prompt engineering|openai api|anthropic api",
        "Machine Learning",
        hours=25,
    ),
    _s("Statistics", "data", "statistics|statistical analysis|a/b testing|hypothesis testing", "", hours=30),
    # CS fundamentals & practices
    _s(
        "Data Structures & Algorithms",
        "cs_fundamentals",
        "data structures|algorithms|dsa|data structures and algorithms|leetcode|competitive programming",
        "",
        hours=80,
        interview="dsa",
    ),
    _s(
        "Object-Oriented Design",
        "cs_fundamentals",
        "object-oriented|object oriented|oop|ood|design patterns|solid principles",
        "",
        hours=20,
        interview="python",
    ),
    _s(
        "Testing",
        "practice",
        "unit testing|integration testing|pytest|jest|tdd|test-driven development|automated testing|unittest|junit",
        "",
        hours=12,
        interview="backend",
    ),
    _s("Agile", "practice", "agile|scrum|kanban|sprint", "", hours=4),
    _s("Code Review", "practice", "code review|code reviews|pull requests", "", hours=4),
    _s(
        "Networking",
        "cs_fundamentals",
        "tcp/ip|http|dns|networking|load balancing|load balancer",
        "",
        hours=15,
        interview="system_design",
    ),
    _s(
        "Operating Systems",
        "cs_fundamentals",
        "operating systems|processes and threads|memory management",
        "",
        hours=20,
    ),
    # Professional skills
    _s(
        "Communication",
        "soft",
        "communication|stakeholder management|presentations|technical writing|documentation",
        "",
        hours=10,
        interview="behavioral",
    ),
    _s(
        "Leadership",
        "soft",
        "leadership|mentoring|mentored|led a team|team lead|tech lead",
        "",
        hours=15,
        interview="behavioral",
    ),
    _s(
        "Problem Solving",
        "soft",
        "problem solving|problem-solving|troubleshooting|debugging|root cause analysis",
        "",
        hours=10,
        interview="behavioral",
    ),
    _s(
        "Collaboration",
        "soft",
        "collaboration|cross-functional|teamwork|cross functional",
        "",
        hours=6,
        interview="behavioral",
    ),
    _s(
        "Behavioral Interviewing",
        "soft",
        "behavioral interview|star method",
        "Communication",
        hours=8,
        interview="behavioral",
    ),
)

BY_NAME: dict[str, SkillDef] = {s.name: s for s in TAXONOMY}

# Aliases that are ordinary English words need extra context to avoid false positives
# ("go to market", "spring semester", "shell company", "node in a tree").
AMBIGUOUS_ALIASES = {
    "go",
    "spring",
    "shell",
    "node",
    "c",
    "rest",
    "cache",
    "express",
    "ts",
    "js",
    "ml",
    "py",
    "logging",
    "tracing",
    "sprint",
    "scalability",
    "concurrency",
    "communication",
    "leadership",
    "documentation",
    "presentations",
    "http",
    "debugging",
    "teamwork",
    "collaboration",
    "lambda",
    "orm",
    "elt",
    "iac",
}
CONTEXT_WORDS = re.compile(
    r"\b(programming|language|languages|developed|built|using|with|in|framework|stack|skills?|experience|"
    r"proficient|knowledge|backend|frontend|services?|apis?|code|wrote|written|engineer(?:ing)?|tools?|technologies)\b",
    re.I,
)


@lru_cache
def _alias_patterns() -> list[tuple[re.Pattern[str], str, str]]:
    """(compiled pattern, canonical skill, alias) sorted longest alias first."""
    pairs: list[tuple[str, str]] = []
    for skill in TAXONOMY:
        for alias in {skill.name.lower(), *skill.aliases}:
            pairs.append((alias.lower(), skill.name))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    out = []
    for alias, name in pairs:
        # Word-ish boundaries that also work for aliases like "c++", "c#", ".net", "ci/cd".
        pat = re.compile(r"(?<![\w+#.])" + re.escape(alias) + r"(?![\w+#])", re.I)
        out.append((pat, name, alias))
    return out


def normalize_skill(raw: str) -> str | None:
    """Map one surface form (e.g. 'postgres', 'React.js') to its canonical name, or None if unknown."""
    key = re.sub(r"\s+", " ", raw.strip().lower()).strip(" .,;:")
    if not key:
        return None
    for skill in TAXONOMY:
        if key == skill.name.lower() or key in skill.aliases:
            return skill.name
    return None


@dataclass
class SkillMention:
    skill: str
    alias: str
    start: int
    end: int


def find_skills(text: str, *, require_context_for_ambiguous: bool = True) -> list[SkillMention]:
    """Find canonical skill mentions in free text. Longest alias wins on overlapping spans.
    Text inside URLs and email addresses is ignored (a github.com link is not evidence of Git)."""
    taken: list[tuple[int, int]] = [m.span() for m in _URLISH.finditer(text)]
    found: list[SkillMention] = []
    for pat, name, alias in _alias_patterns():
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            if require_context_for_ambiguous and alias in AMBIGUOUS_ALIASES and not _ambiguous_ok(text, s, e, alias):
                continue
            taken.append((s, e))
            found.append(SkillMention(name, alias, s, e))
    found.sort(key=lambda m: m.start)
    return found


_URLISH = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|\b[\w-]+\.(?:com|io|dev|org|net)/\S*", re.I)


def _ambiguous_ok(text: str, start: int, end: int, alias: str) -> bool:
    surface = text[start:end]
    before = text[max(0, start - 3) : start].rstrip(" ")
    after = text[end : end + 3].lstrip(" ")
    # "Expense Splitter | Go" or "(Go)" - a capitalised token directly next to a list separator.
    if surface[:1].isupper() and (
        before[-1:] in {",", "|", "/", "(", ":", "•"} or after[:1] in {",", "|", "/", ")", ""} or after[:1] == "\n"
    ):
        return True
    # A short, capitalised token inside a comma/pipe separated list ("Go, Python, Docker") is fine.
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : line_end if line_end != -1 else len(text)]
    separators = sum(line.count(c) for c in ",|•;/")
    if separators >= 2 and len(line) < 300:
        return alias not in {"c", "go"} or surface[0].isupper()
    if alias in {"c", "go", "ts", "js", "py"}:
        return False
    window = text[max(0, start - 60) : end + 60]
    return bool(CONTEXT_WORDS.search(window))


def related_skills(skill: str) -> tuple[str, ...]:
    d = BY_NAME.get(skill)
    return d.related if d else ()


def implied_skills(skill: str) -> tuple[str, ...]:
    d = BY_NAME.get(skill)
    return d.implies if d else ()


def category_of(skill: str) -> str:
    d = BY_NAME.get(skill)
    return d.category if d else "other"


def learn_hours(skill: str) -> float:
    d = BY_NAME.get(skill)
    return d.hours if d else 20.0


def interview_category_for(skill: str) -> str | None:
    d = BY_NAME.get(skill)
    return d.interview_category if d else None
