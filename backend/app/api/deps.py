"""Request dependencies: DB session, authentication, CSRF and rate limiting."""

from __future__ import annotations

import hmac
from collections.abc import Callable

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.rate_limit import client_ip, limiter
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import CandidateProfile, User
from app.repositories.career import get_or_create_profile

CSRF_COOKIE = "cp_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _token_from(request: Request) -> tuple[str | None, bool]:
    """Return (token, via_cookie)."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    return request.cookies.get(get_settings().cookie_name), True


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token, via_cookie = _token_from(request)
    if not token:
        raise UnauthorizedError("Authentication required")
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired session") from exc
    user = db.get(User, int(claims["sub"]))
    if user is None or user.token_version != claims["ver"]:
        raise UnauthorizedError("Session is no longer valid")
    # Cookie-authenticated state changes must carry the double-submit CSRF token.
    if via_cookie and request.method not in SAFE_METHODS:
        cookie = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get(CSRF_HEADER, "")
        if not cookie or not hmac.compare_digest(cookie, header):
            raise ForbiddenError("Missing or invalid CSRF token", code="csrf_failed")
    request.state.user_id = user.id
    return user


def current_profile(user: User = Depends(current_user), db: Session = Depends(get_db)) -> CandidateProfile:
    profile = get_or_create_profile(db, user.id)
    db.commit()
    return profile


def rate_limited(bucket: str, per_minute_setting: str) -> Callable:
    """Per-user (or per-IP when anonymous) sliding-window limit for expensive/sensitive endpoints."""

    def dep(request: Request) -> None:
        limit = getattr(get_settings(), per_minute_setting)
        who = getattr(request.state, "user_id", None) or client_ip(request)
        limiter.check(f"{bucket}:{who}", limit)

    return dep
