"""Bearer access tokens and opaque refresh sessions."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_platform.core.errors import ApiError
from study_platform.persistence.models import User, UserSession

hasher = PasswordHasher()
# Verify nonexistent accounts too, avoiding the cheap account-discovery path.
_dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.settings.allowed_origins:
        raise ApiError(403, "origin_forbidden", "请求来源不被允许")


def account_can_login(user: User | None) -> bool:
    return user is not None and user.status == "active" and user.deleted_at is None


def credentials_valid(user: User | None, password: str) -> bool:
    try:
        valid = hasher.verify(user.password_hash if user else _dummy_hash, password)
    except (VerificationError, InvalidHashError):
        valid = False
    return valid and account_can_login(user)


def signing_key(request: Request) -> str:
    key = request.app.state.settings.auth_jwt_secret.get_secret_value()
    if len(key.encode()) < 32:
        raise ApiError(503, "auth_not_configured", "登录服务尚未配置")
    return key


def issue_token(request: Request, session: UserSession) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"iss": "knowloop", "aud": "knowloop-api", "sub": str(session.user_id),
         "sid": str(session.id), "iat": now, "exp": min(now + timedelta(minutes=15), session.expires_at)},
        signing_key(request), algorithm="HS256", headers={"typ": "at+jwt"},
    )


def create_session(db: Session, user: User, request: Request, response: Response) -> str:
    signing_key(request)
    settings = request.app.state.settings
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=digest(token),
        csrf_secret_hash=digest(csrf),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
    )
    db.add(session)
    user.last_login_at = datetime.now(UTC)
    db.flush()
    access = issue_token(request, session)
    db.commit()
    response.delete_cookie("study_session", path="/")
    response.headers["Cache-Control"] = "no-store"
    for name, value, httponly, path in (
        ("study_refresh", token, True, "/api/v1/auth"),
        ("study_csrf", csrf, False, "/"),
    ):
        response.set_cookie(
            name,
            value,
            max_age=settings.session_ttl_seconds,
            httponly=httponly,
            secure=settings.cookie_secure,
            samesite="lax",
            path=path,
        )
    return access


def find_session(db: Session, request: Request) -> UserSession | None:
    token = request.cookies.get("study_refresh")
    if not token:
        return None
    return db.scalar(
        select(UserSession).where(
            UserSession.refresh_token_hash == digest(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )


def access_session(db: Session, request: Request, token: str | None) -> UserSession | None:
    if not token:
        return None
    key = signing_key(request)
    try:
        decoded = jwt.decode_complete(
            token, key, algorithms=["HS256"], audience="knowloop-api", issuer="knowloop",
            options={"require": ["sub", "sid", "iat", "exp", "iss", "aud"]},
        )
        if decoded["header"].get("typ") != "at+jwt":
            return None
        session_id = UUID(decoded["payload"]["sid"])
        user_id = UUID(decoded["payload"]["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        return None
    return db.scalar(
        select(UserSession).where(
            UserSession.id == session_id, UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None), UserSession.expires_at > datetime.now(UTC),
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
