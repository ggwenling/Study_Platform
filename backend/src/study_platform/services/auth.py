"""Opaque cookie session authentication."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.persistence.models import User, UserSession

hasher = PasswordHasher()
# Verify nonexistent accounts too, avoiding the cheap account-discovery path.
_dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().allowed_origins:
        raise ApiError(403, "origin_forbidden", "请求来源不被允许")


def account_can_login(user: User | None) -> bool:
    return user is not None and user.status == "active" and user.deleted_at is None


def credentials_valid(user: User | None, password: str) -> bool:
    try:
        valid = hasher.verify(user.password_hash if user else _dummy_hash, password)
    except (VerificationError, InvalidHashError):
        valid = False
    return valid and account_can_login(user)


def create_session(db: Session, user: User, response: Response) -> None:
    settings = get_settings()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash=digest(token),
            csrf_secret_hash=digest(csrf),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    for name, value, httponly in (("study_session", token, True), ("study_csrf", csrf, False)):
        response.set_cookie(
            name,
            value,
            max_age=settings.session_ttl_seconds,
            httponly=httponly,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )


def find_session(db: Session, request: Request) -> UserSession | None:
    token = request.cookies.get("study_session")
    if not token:
        return None
    return db.scalar(
        select(UserSession).where(
            UserSession.refresh_token_hash == digest(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )


def check_csrf(request: Request, session: UserSession) -> None:
    check_origin(request)
    cookie, header = request.cookies.get("study_csrf"), request.headers.get("x-csrf-token")
    if (
        not cookie
        or not header
        or not secrets.compare_digest(cookie, header)
        or not secrets.compare_digest(digest(header), session.csrf_secret_hash)
    ):
        raise ApiError(403, "csrf_invalid", "请求验证失败，请刷新后重试")
