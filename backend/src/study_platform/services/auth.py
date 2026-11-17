"""Bearer access tokens and opaque refresh sessions."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
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


def allowed_request_origin(request: Request) -> None:
    """Require a whitelisted Origin or Referer before issuing session cookies."""
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin:
        if origin not in settings.allowed_origins:
            raise ApiError(403, "origin_forbidden", "请求来源不被允许")
        return
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            ref_origin = f"{parsed.scheme}://{parsed.netloc}"
            if ref_origin in settings.allowed_origins:
                return
    raise ApiError(403, "origin_required", "请求来源不明确，请从受信任页面重试")


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
        {
            "iss": "knowloop",
            "aud": "knowloop-api",
            "sub": str(session.user_id),
            "sid": str(session.id),
            "tg": session.access_generation,
            "iat": now,
            "exp": min(now + timedelta(minutes=15), session.expires_at),
        },
        signing_key(request),
        algorithm="HS256",
        headers={"typ": "at+jwt"},
    )


def set_session_cookies(
    response: Response, settings, refresh_token: str, csrf_token: str
) -> None:
    response.delete_cookie("study_session", path="/")
    response.headers["Cache-Control"] = "no-store"
    for name, value, httponly, path in (
        ("study_refresh", refresh_token, True, "/api/v1/auth"),
        ("study_csrf", csrf_token, True, "/"),
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


def create_session(db: Session, user: User, request: Request, response: Response) -> tuple[str, str]:
    signing_key(request)
    settings = request.app.state.settings
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    user_agent = request.headers.get("user-agent", "")
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=digest(token),
        csrf_secret_hash=digest(csrf),
        user_agent_hash=digest(user_agent) if user_agent else None,
        access_generation=0,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
    )
    db.add(session)
    user.last_login_at = datetime.now(UTC)
    db.flush()
    access = issue_token(request, session)
    db.commit()
    set_session_cookies(response, settings, token, csrf)
    return access, csrf


def rotate_session(db: Session, session: UserSession, request: Request, response: Response) -> tuple[str, str]:
    settings = request.app.state.settings
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.refresh_token_hash = digest(token)
    session.csrf_secret_hash = digest(csrf)
    session.access_generation += 1
    user_agent = request.headers.get("user-agent", "")
    if user_agent:
        session.user_agent_hash = digest(user_agent)
    access = issue_token(request, session)
    db.commit()
    set_session_cookies(response, settings, token, csrf)
    return access, csrf


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
            token,
            key,
            algorithms=["HS256"],
            audience="knowloop-api",
            issuer="knowloop",
            options={"require": ["sub", "sid", "iat", "exp", "iss", "aud"]},
        )
        if decoded["header"].get("typ") != "at+jwt":
            return None
        session_id = UUID(decoded["payload"]["sid"])
        user_id = UUID(decoded["payload"]["sub"])
        generation = int(decoded["payload"].get("tg", 0))
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        return None
    session = db.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None or session.access_generation != generation:
        return None
    return session


def check_csrf(request: Request, session: UserSession) -> None:
    allowed_request_origin(request)
    cookie, header = request.cookies.get("study_csrf"), request.headers.get("x-csrf-token")
    if (
        not cookie
        or not header
        or not secrets.compare_digest(cookie, header)
        or not secrets.compare_digest(digest(header), session.csrf_secret_hash)
    ):
        raise ApiError(403, "csrf_invalid", "请求验证失败，请刷新后重试")


def validate_cookie_security(settings) -> None:
    public = settings.auth_public_url.strip()
    if public.startswith("https://") and not settings.cookie_secure:
        raise RuntimeError(
            "AUTH_PUBLIC_URL 使用 HTTPS 时必须设置 COOKIE_SECURE=true，否则会话 Cookie 可能在明文连接上泄露"
        )