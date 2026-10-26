"""Test configuration.

Uses PostgreSQL (TEST_DATABASE_URL, default: the local dev server's careerpilot_test database)
with real Alembic migrations when reachable; otherwise falls back to SQLite + create_all.
All providers are deterministic mocks: no network or paid API calls.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="careerpilot-tests-"))
PG_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:54329/careerpilot_test")


def _pg_available(url: str) -> bool:
    try:
        import psycopg

        with psycopg.connect(url.replace("+psycopg", ""), connect_timeout=2):
            return True
    except Exception:
        return False


DB_URL = PG_URL if _pg_available(PG_URL) else f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": DB_URL,
        "CHECKPOINTER": "memory",
        "WORKFLOW_INLINE": "true",
        "LLM_PROVIDER": "mock",
        "JOB_PROVIDERS": "mock",
        "STORAGE_DIR": str(TMP / "uploads"),
        "CODE_SANDBOX": "disabled",
        "RATE_LIMIT_AUTH_PER_MINUTE": "1000",
        "RATE_LIMIT_EXPENSIVE_PER_MINUTE": "1000",
        "JWT_SECRET": "t" * 40,
    }
)

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.db.session import configure_engine  # noqa: E402


def pytest_report_header(config) -> str:  # noqa: ANN001
    return f"database: {'postgresql' if DB_URL.startswith('postgresql') else 'sqlite'}"


@pytest.fixture(scope="session", autouse=True)
def _database():
    engine = configure_engine(DB_URL)
    if DB_URL.startswith("postgresql"):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        cfg.set_main_option("sqlalchemy.url", DB_URL)
        command.upgrade(cfg, "head")
    else:
        Base.metadata.create_all(engine)
    yield engine


@pytest.fixture(autouse=True)
def _clean(_database):
    from sqlalchemy import text

    from app.core.rate_limit import limiter
    from app.graph.checkpoint import reset_checkpointer
    from app.graph.runner import runner
    from app.services.llm import set_llm

    yield
    limiter.reset()
    set_llm(None)
    reset_checkpointer()
    runner.reset()
    from app.api.routes.interviews import reset_interview_graph

    reset_interview_graph()
    with _database.begin() as conn:
        if DB_URL.startswith("postgresql"):
            names = ", ".join(t.name for t in Base.metadata.sorted_tables)
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for t in reversed(Base.metadata.sorted_tables):
                conn.execute(t.delete())


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _register(c, email: str = "alice@example.com", password: str = "correct-horse-1", name: str = "Alice"):  # noqa: ANN001
    r = c.post("/api/auth/register", json={"email": email, "password": password, "full_name": name})
    assert r.status_code == 201, r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("cp_csrf")
    return r.json()


@pytest.fixture
def auth_client(client):
    _register(client)
    return client


@pytest.fixture
def make_client():
    """Factory for additional independent authenticated clients (multi-user permission tests)."""
    from fastapi.testclient import TestClient

    from app.main import app

    made = []

    def factory(email: str):
        c = TestClient(app)
        c.__enter__()
        made.append(c)
        _register(c, email=email)
        return c

    yield factory
    for c in made:
        c.__exit__(None, None, None)


SAMPLE_RESUME = (Path(__file__).resolve().parents[1] / "app" / "demo" / "sample_resume.txt").read_text(encoding="utf-8")


def make_pdf(text: str) -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.splitlines():
        c.drawString(40, y, line[:110])
        y -= 14
        if y < 40:
            c.showPage()
            y = 800
    c.save()
    return buf.getvalue()


def make_docx(text: str) -> bytes:
    import io

    from docx import Document

    d = Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def onboard(c, target_role: str = "Backend Engineer", weeks: int = 10) -> None:  # noqa: ANN001
    from datetime import date, timedelta

    r = c.post(
        "/api/profile/onboarding",
        json={
            "profile": {"target_role": target_role, "headline": "Software Engineer", "location": "Bengaluru, India"},
            "preferences": {
                "weekly_study_hours": 8,
                "study_days_per_week": 5,
                "target_deadline": (date.today() + timedelta(weeks=weeks)).isoformat(),
                "preferred_locations": ["Bengaluru"],
                "work_arrangements": ["hybrid", "remote"],
            },
        },
    )
    assert r.status_code == 200, r.text
