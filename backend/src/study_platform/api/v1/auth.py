"""Registration, login, logout and current user endpoints."""

from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User
from study_platform.services.auth import (
    access_session,
    account_can_login,
    allowed_request_origin,
    check_csrf,
    create_session,
    credentials_valid,
    find_session,
    hasher,
    rotate_session,
    signing_key,
)
from study_platform.services.auth_limits import rate_limit_auth

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


class UserDTO(BaseModel):
    id: str
    email: str
    display_name: str
    avatar_url: str | None = None


class AuthDTO(BaseModel):
    user: UserDTO
    access_token: str
    csrf_token: str


class LoginErrorDTO(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        try:
            return validate_email(value.strip(), check_deliverability=False, test_environment=True).normalized.lower()
        except EmailNotValidError:
            raise ValueError("邮箱格式无效") from None


class RegisterBody(LoginBody):
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入姓名")
        return value


def user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": None,
    }


def get_current_user(
    request: Request, db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    session = access_session(db, request, credentials.credentials if credentials else None)
    user = db.get(User, session.user_id) if session else None
    if user is None or not account_can_login(user):
        raise ApiError(401, "unauthenticated", "请先登录")
    # A streaming response can outlive authentication by minutes. Return the
    # loaded user without keeping a read transaction or pool slot open.
    db.expunge(user)
    db.rollback()
    return user


@router.get("/me", response_model=UserDTO)
def me(user: User = Depends(get_current_user)) -> dict:
    return user_payload(user)


@router.post("/auth/register", status_code=201, response_model=AuthDTO)
def register(
    body: RegisterBody, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict:
    rate_limit_auth(request, limit=request.app.state.settings.auth_rate_limit_per_minute)
    allowed_request_origin(request)
    signing_key(request)
    user = User(
        email=body.email, password_hash=hasher.hash(body.password), display_name=body.display_name
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(400, "registration_failed", "无法注册，请检查输入或尝试登录") from None
    access, csrf = create_session(db, user, request, response)
    return {"user": user_payload(user), "access_token": access, "csrf_token": csrf}


@router.post(
    "/auth/login",
    response_model=AuthDTO,
    responses={
        401: {
            "model": LoginErrorDTO,
            "description": (
                "凭据错误。默认返回 invalid_credentials。启用 LOGIN_DETAILED_ERRORS 后，"
                "账号不可登录返回 invalid_account，密码错误返回 invalid_password。"
            ),
        },
        403: {"model": LoginErrorDTO, "description": "请求来源不被允许。"},
        422: {"model": LoginErrorDTO, "description": "请求字段缺失或格式不正确。"},
    },
)
def login(
    body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict:
    rate_limit_auth(request, limit=request.app.state.settings.auth_rate_limit_per_minute)
    allowed_request_origin(request)
    user = db.scalar(select(User).where(User.email == body.email).with_for_update())
    if not credentials_valid(user, body.password):
        if request.app.state.settings.login_detailed_errors:
            if not account_can_login(user):
                raise ApiError(401, "invalid_account", "此邮箱对应的账号不可登录")
            raise ApiError(401, "invalid_password", "密码错误")
        raise ApiError(401, "invalid_credentials", "邮箱或密码错误")
    access, csrf = create_session(db, user, request, response)
    return {"user": user_payload(user), "access_token": access, "csrf_token": csrf}


@router.post("/auth/refresh", response_model=AuthDTO)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    session = find_session(db, request)
    user = db.get(User, session.user_id) if session else None
    if not session or user is None or not account_can_login(user):
        raise ApiError(401, "unauthenticated", "请先登录")
    check_csrf(request, session)
    response.headers["Cache-Control"] = "no-store"
    access, csrf = rotate_session(db, session, request, response)
    return {"user": user_payload(user), "access_token": access, "csrf_token": csrf}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    session = find_session(db, request)
    if session:
        check_csrf(request, session)
    if session:
        session.revoked_at = datetime.now(UTC)
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie("study_refresh", path="/api/v1/auth")
    response.delete_cookie("study_session", path="/")
    response.delete_cookie("study_csrf", path="/")
    return response
