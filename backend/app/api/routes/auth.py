"""Registration, login, logout and account deletion."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CSRF_COOKIE, current_user, rate_limited
from app.core.config import get_settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.logging import get_logger, log_ctx
from app.core.rate_limit import client_ip, limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import CandidateProfile, Resume, User
from app.schemas.api import AuthOut, DeleteAccountIn, LoginIn, RegisterIn, UserOut
from app.services.storage import get_storage

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)
# Verifying against a dummy hash keeps login timing similar for unknown emails.
_DUMMY_HASH = hash_password("not-a-real-password-0")


def _user_out(db: Session, user: User) -> UserOut:
    onboarded = db.scalar(select(CandidateProfile.onboarding_completed).where(CandidateProfile.user_id == user.id))
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_demo=user.is_demo,
        created_at=user.created_at,
        onboarding_completed=bool(onboarded),
    )


def _set_session(response: Response, user: User) -> str:
    s = get_settings()
    token = create_access_token(user.id, user.token_version)
    max_age = s.access_token_ttl_minutes * 60
    response.set_cookie(
        s.cookie_name, token, max_age=max_age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/"
    )
    # Readable by the SPA so it can echo it in X-CSRF-Token (double-submit pattern).
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(24),
        max_age=max_age,
        httponly=False,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )
    return token


@router.post(
    "/register",
    response_model=AuthOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited("auth", "rate_limit_auth_per_minute"))],
)
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)) -> AuthOut:
    email = body.email.lower()
    if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise ConflictError("An account with this email already exists")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name.strip())
    db.add(user)
    db.flush()
    db.add(CandidateProfile(user_id=user.id))
    db.commit()
    token = _set_session(response, user)
    logger.info("User registered", extra=log_ctx(user_id=user.id))
    return AuthOut(user=_user_out(db, user), access_token=token)


@router.post("/login", response_model=AuthOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> AuthOut:
    s = get_settings()
    limiter.check(f"login-ip:{client_ip(request)}", s.rate_limit_auth_per_minute)
    limiter.check(f"login-email:{body.email.lower()}", s.rate_limit_auth_per_minute)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise UnauthorizedError("Invalid email or password")
    if not verify_password(body.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")
    user.last_login_at = datetime.now(UTC)
    db.commit()
    token = _set_session(response, user)
    return AuthOut(user=_user_out(db, user), access_token=token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    s = get_settings()
    response.delete_cookie(s.cookie_name, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    user.token_version += 1
    db.commit()
    return logout(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    return _user_out(db, user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    body: DeleteAccountIn, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """Permanently delete the account, all profile data (cascades) and stored resume files."""
    if not verify_password(body.password, user.password_hash):
        raise UnauthorizedError("Password is incorrect")
    keys = list(
        db.scalars(
            select(Resume.storage_key)
            .join(CandidateProfile, Resume.profile_id == CandidateProfile.id)
            .where(CandidateProfile.user_id == user.id)
        )
    )
    db.delete(user)
    db.commit()
    storage = get_storage()
    for key in keys:
        storage.delete(key)
    logger.info("Account deleted", extra=log_ctx(user_id=user.id, files=len(keys)))
    return logout(response)
