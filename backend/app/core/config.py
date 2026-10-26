"""Application settings, loaded from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "CareerPilot AI"
    api_prefix: str = "/api"

    database_url: str = "postgresql+psycopg://postgres@127.0.0.1:54329/careerpilot"
    # Checkpointer for LangGraph. "auto" -> postgres when database_url is postgres, otherwise sqlite file.
    checkpointer: Literal["auto", "postgres", "sqlite", "memory"] = "auto"
    checkpoint_sqlite_path: str = str(BACKEND_DIR / "var" / "checkpoints.sqlite")

    # Auth
    jwt_secret: str = Field(default="dev-only-change-me-dev-only-change-me", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    cookie_name: str = "cp_session"
    cookie_secure: bool = False
    cors_origins: CsvList = ["http://localhost:3000"]

    # LLM
    llm_provider: Literal["mock", "anthropic", "gemini"] = "mock"
    llm_model: str = "claude-opus-5-5"  # Anthropic model
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_use_fallbacks: bool = True
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-latest"  # alias to the current stable Flash model
    # Extraction/grading are simple tasks; lower thinking cuts latency a lot. "" = model default.
    # Tried in order when the primary model is rate limited or overloaded (429/503/504).
    gemini_fallback_models: CsvList = ["gemini-3.5-flash", "gemini-flash-lite-latest"]
    gemini_thinking_level: Literal["", "minimal", "low", "medium", "high"] = "low"

    # Embeddings: "hash" is deterministic feature hashing (no external calls).
    embedding_provider: Literal["hash"] = "hash"
    embedding_dim: int = 256

    # Job research
    job_providers: CsvList = ["mock"]
    greenhouse_boards: CsvList = []
    lever_companies: CsvList = []
    job_stale_after_days: int = 30
    job_http_timeout_seconds: float = 15.0

    # Uploads / storage
    storage_dir: str = str(BACKEND_DIR / "var" / "uploads")
    max_upload_mb: int = 5

    # Workflow
    max_revision_loops: int = 2
    workflow_workers: int = 4
    # Run workflows synchronously inside the request (tests / debugging). Default is background threads.
    workflow_inline: bool = False

    # Code sandbox: "docker" runs submissions in a network-less container; "disabled" does static checks only.
    code_sandbox: Literal["docker", "disabled"] = "disabled"
    sandbox_image: str = "python:3.12-alpine"
    sandbox_timeout_seconds: int = 5

    # Data retention (days). 0 disables automatic purge.
    resume_retention_days: int = 365
    workflow_retention_days: int = 90

    # Rate limits (requests per minute per user/IP)
    rate_limit_auth_per_minute: int = 10
    rate_limit_expensive_per_minute: int = 6

    demo_mode: bool = True

    @field_validator(
        "cors_origins", "job_providers", "greenhouse_boards", "lever_companies", "gemini_fallback_models", mode="before"
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    def active_llm(self) -> tuple[str, str | None]:
        """(provider, model) actually in use: falls back to mock when the selected provider has no key."""
        if self.llm_provider == "anthropic" and self.anthropic_api_key:
            return "anthropic", self.llm_model
        if self.llm_provider == "gemini" and self.gemini_api_key:
            return "gemini", self.gemini_model
        return "mock", None

    @field_validator("database_url", mode="before")
    @classmethod
    def _psycopg_driver(cls, v: object) -> object:
        # Hosted Postgres (Neon, Render, ...) hands out postgres:// or postgresql:// URLs.
        if isinstance(v, str):
            for prefix in ("postgres://", "postgresql://"):
                if v.startswith(prefix):
                    return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
