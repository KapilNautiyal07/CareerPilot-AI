"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import applications, auth, dashboard, interviews, jobs, learning, meta, profile, resumes, workflows
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger, log_ctx

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging()
    if settings.environment == "production" and settings.jwt_secret.startswith("dev-only"):
        raise RuntimeError("JWT_SECRET must be set in production")
    from app.db.session import session_scope
    from app.graph.runner import runner
    from app.repositories.career import seed_resources
    from app.services.retention import purge_expired

    with session_scope() as db:
        seed_resources(db)
    purged = purge_expired()
    recovered = runner.recover()
    logger.info("Startup complete", extra=log_ctx(recovered_workflows=recovered, **purged))
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="CareerPilot AI API",
        version="0.1.0",
        lifespan=lifespan,
        description="Multi-agent career intelligence: resume analysis, job research, skill gaps, roadmaps, "
        "mock interviews and evaluation. All user data endpoints require authentication.",
        openapi_url=f"{s.api_prefix}/openapi.json",
        docs_url=f"{s.api_prefix}/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    install_error_handlers(app)
    for r in (auth, profile, resumes, jobs, learning, interviews, applications, dashboard, workflows, meta):
        app.include_router(r.router, prefix=s.api_prefix)
    return app


app = create_app()
