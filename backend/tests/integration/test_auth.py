"""Cookie and CSRF integration checks in an explicitly isolated test database."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import Settings
from study_platform.main import create_app
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User, UserSession
from study_platform.services.auth import digest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(
        url.query
    ):
        pytest.fail("isolated *_test database required")
    url = url.set(drivername="postgresql+psycopg")
    engine = sa.create_engine(url)
    monkeypatch.delenv("LOGIN_DETAILED_ERRORS", raising=False)
    settings = Settings(_env_file=None, database_url=url.render_as_string(hide_password=False))
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(connection, join_transaction_mode="create_savepoint")
        app = create_app(settings)
        app.dependency_overrides[get_db] = lambda: db

        @app.post("/api/v1/protected")
        def protected(user=Depends(get_current_user)):
            return {"id": str(user.id)}

        with TestClient(app, base_url="http://testserver/api/v1") as result:
            yield result, db
        db.close()
        transaction.rollback()
    engine.dispose()


def test_cookie_csrf_and_user_isolation(client):
    http, db = client
    body = {
        "email": f"{uuid4().hex}@example.test",
        "password": "safe-password-123",
        "display_name": "Tester",
    }
    response = http.post("/auth/register", json=body)
    assert response.status_code == 201
    first = response.json()["id"]
    cookies = response.headers.get_list("set-cookie")
    assert any("study_session=" in c and "HttpOnly" in c and "SameSite=lax" in c for c in cookies)
    assert any("study_csrf=" in c and "HttpOnly" not in c for c in cookies)
    token = http.cookies["study_session"]
    saved = db.scalar(sa.select(UserSession).where(UserSession.refresh_token_hash == digest(token)))
    assert saved and saved.refresh_token_hash != token
    assert http.get("/me").json()["id"] == first
    assert http.post("/protected").status_code == 403
    csrf = {"X-CSRF-Token": http.cookies["study_csrf"]}
    assert http.post("/protected", headers=csrf).status_code == 200
    assert (
        http.post("/protected", headers={**csrf, "Origin": "https://evil.test"}).status_code == 403
    )
    assert http.post("/auth/logout", headers=csrf).status_code == 204
    assert http.get("/me").status_code == 401
    assert http.post("/auth/logout").status_code == 204
    body["email"] = f"{uuid4().hex}@example.test"
    second = http.post("/auth/register", json=body).json()["id"]
    assert second != first
    assert http.get("/me").json()["id"] == second


@pytest.mark.parametrize("detailed", [False, True])
def test_login_errors_and_session_creation(client, detailed):
    http, db = client
    assert http.app.state.settings.login_detailed_errors is False
    http.app.state.settings.login_detailed_errors = detailed
    body = {"email": f"{uuid4().hex}@example.test", "password": "safe-password-123"}
    blocked = http.post(
        "/auth/register", json={**body, "display_name": "Tester"},
        headers={"Origin": "https://evil.test"},
    )
    assert blocked.status_code == 403
    registered = http.post("/auth/register", json={**body, "display_name": "Tester"})
    assert registered.status_code == 201
    user = db.scalar(sa.select(User).where(User.email == body["email"]))
    assert http.post("/auth/logout", headers={"X-CSRF-Token": http.cookies["study_csrf"]}).status_code == 204
    sessions = sa.select(sa.func.count()).select_from(UserSession)
    original_count = db.scalar(sessions)

    def failed(credentials, status, code, message, **kwargs):
        result = http.post("/auth/login", json=credentials, **kwargs)
        assert result.status_code == status
        payload = result.json()
        assert set(payload) == {"code", "message", "request_id", "retryable"}
        assert payload["code"] == code
        assert payload["message"] == message
        assert payload["request_id"] and payload["retryable"] is False
        assert "set-cookie" not in result.headers
        assert db.scalar(sessions) == original_count

    generic = ("invalid_credentials", "邮箱或密码错误")
    account = ("invalid_account", "此邮箱对应的账号不可登录") if detailed else generic
    password = ("invalid_password", "密码错误") if detailed else generic
    wrong = {**body, "password": "wrong-password-456"}
    failed({**wrong, "email": f"{uuid4().hex}@example.test"}, 401, *account)
    failed(wrong, 401, *password)
    user.status = "disabled"
    db.commit()
    failed(body, 401, *account)
    user.status = "active"
    user.deleted_at = datetime.now(UTC)
    db.commit()
    failed(body, 401, *account)
    user.deleted_at = None
    db.commit()
    failed(body, 403, "origin_forbidden", "请求来源不被允许", headers={"Origin": "https://evil.test"})
    failed({"email": body["email"]}, 422, "validation_error", "请求字段缺失或格式不正确")
    failed({**body, "email": "invalid-email"}, 422, "validation_error", "请求字段缺失或格式不正确")
    assert http.get("/me").status_code == 401

    response = http.post("/auth/login", json={**body, "email": body["email"].upper()})
    assert response.status_code == 200
    assert response.json() == registered.json()
    assert "study_session" in response.cookies and "study_csrf" in response.cookies
    assert db.scalar(sessions) == original_count + 1
    assert http.get("/me").json() == registered.json()


def test_expired_revoked_disabled_and_deleted_sessions(client):
    http, db = client
    body = {
        "email": f"{uuid4().hex}@example.test",
        "password": "safe-password-123",
        "display_name": "Tester",
    }
    assert http.post("/auth/register", json=body).status_code == 201
    session = db.scalar(
        sa.select(UserSession).where(
            UserSession.refresh_token_hash == digest(http.cookies["study_session"])
        )
    )
    user = db.get(User, session.user_id)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert http.get("/me").status_code == 401
    session.expires_at = datetime.now(UTC) + timedelta(days=1)
    session.revoked_at = datetime.now(UTC)
    db.commit()
    assert http.get("/me").status_code == 401
    session.revoked_at = None
    user.status = "disabled"
    db.commit()
    assert http.get("/me").status_code == 401
    assert http.post("/auth/login", json=body).status_code == 401
    user.status = "active"
    user.deleted_at = datetime.now(UTC)
    db.commit()
    assert http.get("/me").status_code == 401
    assert http.post("/auth/login", json=body).status_code == 401
