"""Google sign-in and email password recovery."""

import base64
import hashlib
import logging
import secrets
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import AuthDTO, user_payload
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User, UserSession
from study_platform.persistence.models.identity import GoogleIdentity, PasswordReset
from study_platform.services.auth import (
    account_can_login,
    allowed_request_origin,
    check_origin,
    create_session,
    credentials_valid,
    digest,
    hasher,
    signing_key,
)

router = APIRouter(prefix="/auth")
logger = logging.getLogger(__name__)
GOOGLE = "https://accounts.google.com"
GOOGLE_KEYS = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", timeout=8)
FLOW_COOKIE = "study_google_flow"
LINK_COOKIE = "study_google_link"
COOKIE_PATH = "/api/v1/auth/google"


class AuthOptions(BaseModel):
    google: bool
    password_reset: bool


@router.get("/options")
def auth_options(request: Request) -> AuthOptions:
    settings = request.app.state.settings
    return AuthOptions(
        google=bool(settings.google_client_id and settings.google_client_secret.get_secret_value()),
        password_reset=bool(settings.smtp_host and settings.smtp_from),
    )


def safe_path(path: str) -> str:
    if not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(char) < 32 for char in path):
        return "/"
    parts = urlsplit(path)
    if parts.scheme or parts.netloc:
        return "/"
    return parts.path + (f"?{parts.query}" if parts.query else "")


def redirect(request: Request, path: str = "/", **params: str) -> RedirectResponse:
    base = request.app.state.settings.auth_public_url.rstrip("/")
    target = safe_path(path)
    query = ("&" if "?" in target else "?") + urlencode(params) if params else ""
    return RedirectResponse(base + target + query, status_code=303, headers={"Cache-Control": "no-store"})


def set_flow(request: Request, response: Response, cookie: str, data: dict) -> None:
    token = jwt.encode(
        {**data, "aud": cookie, "exp": datetime.now(UTC) + timedelta(minutes=5)},
        signing_key(request), algorithm="HS256",
    )
    response.set_cookie(
        cookie, token, max_age=300, path=COOKIE_PATH, httponly=True,
        secure=request.app.state.settings.cookie_secure, samesite="lax",
    )


def get_flow(request: Request, cookie: str) -> dict:
    try:
        return jwt.decode(
            request.cookies.get(cookie, ""), signing_key(request),
            algorithms=["HS256"], audience=cookie, options={"require": ["exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise ApiError(400, "google_flow_expired", "Google 登录已过期，请重新开始") from exc


def callback_url(request: Request) -> str:
    return request.app.state.settings.auth_public_url.rstrip("/") + "/api/v1/auth/google/callback"


@router.get("/google/start")
def google_start(request: Request, next: str = "/") -> RedirectResponse:
    if not auth_options(request).google:
        raise ApiError(503, "google_unavailable", "Google 登录尚未配置")
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    options = {
        "client_id": request.app.state.settings.google_client_id,
        "redirect_uri": callback_url(request),
        "scope": "openid email profile",
        "response_type": "code", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    response = RedirectResponse(f"{GOOGLE}/o/oauth2/v2/auth?{urlencode(options)}", status_code=303)
    set_flow(request, response, FLOW_COOKIE, {
        "state": state, "nonce": nonce, "verifier": verifier, "next": safe_path(next),
    })
    response.delete_cookie(LINK_COOKIE, path=COOKIE_PATH)
    return response


def google_profile(request: Request, code: str, flow: dict) -> tuple[str, str, str]:
    settings = request.app.state.settings
    reply = httpx.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(request),
        "client_id": settings.google_client_id, "client_secret": settings.google_client_secret.get_secret_value(),
        "code_verifier": flow["verifier"],
    }, timeout=8)
    reply.raise_for_status()
    token = reply.json()["id_token"]
    claims = jwt.decode(
        token, GOOGLE_KEYS.get_signing_key_from_jwt(token).key,
        algorithms=["RS256"], audience=settings.google_client_id,
        issuer=[GOOGLE, "accounts.google.com"], options={"require": ["exp", "iat", "sub", "nonce", "email"]},
    )
    if claims.get("email_verified") is not True or claims["nonce"] != flow["nonce"]:
        raise ValueError("Google identity verification failed")
    if claims.get("azp", settings.google_client_id) != settings.google_client_id:
        raise ValueError("Google authorized party mismatch")
    email = clean_email(claims["email"])
    return claims["sub"], email, str(claims.get("name") or email.split("@")[0])[:100]


@router.get("/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    try:
        flow = get_flow(request, FLOW_COOKIE)
    except ApiError:
        flow = {}
    path = flow.get("next", "/")
    response = redirect(request, path, auth="google_error")
    try:
        if code and secrets.compare_digest(state, flow.get("state", "")):
            subject, email, name = google_profile(request, code, flow)
            identity = db.get(GoogleIdentity, subject)
            user = db.get(User, identity.user_id, with_for_update=True) if identity else db.scalar(select(User).where(User.email == email))
            if identity and account_can_login(user):
                response = redirect(request, path)
                create_session(db, user, request, response)
            elif user is None:
                user = User(email=email, display_name=name, password_hash=hasher.hash(secrets.token_urlsafe(32)),
                            email_verified_at=datetime.now(UTC))
                db.add(user)
                db.flush()
                db.add(GoogleIdentity(subject=subject, user_id=user.id))
                response = redirect(request, path)
                create_session(db, user, request, response)
            elif not identity and account_can_login(user):
                response = redirect(request, path, auth="google_link")
                set_flow(request, response, LINK_COOKIE, {"subject": subject, "uid": str(user.id)})
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, IntegrityError):
        db.rollback()
        response = redirect(request, path, auth="google_error")
    response.delete_cookie(FLOW_COOKIE, path=COOKIE_PATH)
    return response


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.post("/google/link", response_model=AuthDTO)
def google_link(body: PasswordBody, request: Request, response: Response, db: Session = Depends(get_db)):
    allowed_request_origin(request)
    flow = get_flow(request, LINK_COOKIE)
    user = db.get(User, UUID(flow["uid"]), with_for_update=True)
    if not credentials_valid(user, body.password):
        raise ApiError(401, "invalid_password", "密码错误")
    db.add(GoogleIdentity(subject=flow["subject"], user_id=user.id))
    try:
        access, csrf = create_session(db, user, request, response)
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "google_already_linked", "此账号已有 Google 绑定") from exc
    response.delete_cookie(LINK_COOKIE, path=COOKIE_PATH)
    return {"user": user_payload(user), "access_token": access, "csrf_token": csrf}


def clean_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False, test_environment=True).normalized.lower()
    except EmailNotValidError:
        raise ValueError("邮箱格式无效") from None


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return clean_email(value)


class ResetPassword(PasswordBody):
    token: str = Field(min_length=32, max_length=128)


def send_reset_email(settings, address: str, token: str) -> None:
    link = settings.auth_public_url.rstrip("/") + "/#reset=" + token
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = address
    message["Subject"] = "重置知循密码"
    message.set_content(f"请在 30 分钟内打开链接设置新密码：\n{link}\n\n如非本人操作，请忽略此邮件。")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        logger.exception("Password reset email delivery failed")


@router.post("/password/forgot", status_code=202)
def forgot_password(
    body: ResetRequest, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db),
) -> dict[str, str]:
    check_origin(request)
    if not auth_options(request).password_reset:
        raise ApiError(503, "password_reset_unavailable", "找回密码尚未配置")
    now = datetime.now(UTC)
    email_hash = digest(body.email)
    client_hash = digest(request.client.host if request.client else "unknown")
    reply = {"message": "如果此邮箱可用于找回密码，你会收到重置邮件。"}
    for key in sorted({email_hash, client_hash}):
        db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True))))
    for column, key, limit in ((PasswordReset.email_hash, email_hash, 1), (PasswordReset.client_hash, client_hash, 10)):
        count = db.scalar(select(func.count()).select_from(PasswordReset).where(
            column == key, PasswordReset.created_at > now - timedelta(minutes=5),
        ))
        if count >= limit:
            return reply
    user = db.scalar(select(User).where(User.email == body.email))
    token = secrets.token_urlsafe(32)
    enabled = account_can_login(user)
    db.add(PasswordReset(token_hash=digest(token), user_id=user.id if enabled else None,
                         email_hash=email_hash, client_hash=client_hash, created_at=now))
    db.commit()
    if enabled:
        tasks.add_task(send_reset_email, request.app.state.settings, body.email, token)
    return reply


@router.post("/password/reset", status_code=204)
def reset_password(body: ResetPassword, request: Request, db: Session = Depends(get_db)) -> Response:
    check_origin(request)
    now = datetime.now(UTC)
    row = db.get(PasswordReset, digest(body.token))
    user = db.get(User, row.user_id, with_for_update=True) if row and row.user_id else None
    if not account_can_login(user):
        raise ApiError(400, "invalid_reset", "重置链接无效或已过期")
    updated = db.execute(update(PasswordReset).where(
        PasswordReset.token_hash == digest(body.token), PasswordReset.used_at.is_(None),
        PasswordReset.created_at > now - timedelta(minutes=30),
    ).values(used_at=now))
    if updated.rowcount != 1:
        raise ApiError(400, "invalid_reset", "重置链接无效或已过期")
    user.password_hash = hasher.hash(body.password)
    db.execute(update(PasswordReset).where(
        PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None),
    ).values(used_at=now))
    db.execute(update(UserSession).where(
        UserSession.user_id == user.id, UserSession.revoked_at.is_(None),
    ).values(revoked_at=now))
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie("study_refresh", path="/api/v1/auth")
    response.delete_cookie("study_csrf", path="/")
    return response
