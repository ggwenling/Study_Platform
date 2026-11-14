"""Registration, login, logout and current user endpoints."""

from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User
from study_platform.services.auth import (
    account_can_login,
    check_csrf,
    check_origin,
    create_session,
    credentials_valid,
    find_session,
    hasher,
)

router = APIRouter()


class UserDTO(BaseModel):
    id: str
    email: str
    display_name: str
    avatar_url: str | None = None


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


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    session = find_session(db, request)
    user = db.get(User, session.user_id) if session else None
    if user is None or not account_can_login(user):
        raise ApiError(401, "unauthenticated", "请先登录")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_csrf(request, session)
    # A streaming response can outlive authentication by minutes. Return the
    # loaded user without keeping a read transaction or pool slot open.
    db.expunge(user)
    db.rollback()
    return user


@router.get("/me", response_model=UserDTO)
def me(user: User = Depends(get_current_user)) -> dict:
    return user_payload(user)


@router.post("/auth/register", status_code=201, response_model=UserDTO)
def register(
    body: RegisterBody, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict:
    check_origin(request)
    user = User(
        email=body.email, password_hash=hasher.hash(body.password), display_name=body.display_name
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(400, "registration_failed", "无法注册，请检查输入或尝试登录") from None
    create_session(db, user, response)
    return user_payload(user)


@router.post(
    "/auth/login",
    response_model=UserDTO,
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
    check_origin(request)
    user = db.scalar(select(User).where(User.email == body.email))
    if not credentials_valid(user, body.password):
        if request.app.state.settings.login_detailed_errors:
            if not account_can_login(user):
                raise ApiError(401, "invalid_account", "此邮箱对应的账号不可登录")
            raise ApiError(401, "invalid_password", "密码错误")
        raise ApiError(401, "invalid_credentials", "邮箱或密码错误")
    create_session(db, user, response)
    return user_payload(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    check_origin(request)
    session = find_session(db, request)
    if session:
        check_csrf(request, session)
        session.revoked_at = datetime.now(UTC)
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie("study_session", path="/")
    response.delete_cookie("study_csrf", path="/")
    return response
