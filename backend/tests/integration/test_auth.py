"""Cookie and CSRF integration checks in an explicitly isolated test database."""

import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from study_platform.api.v1.auth import get_current_user, router
from study_platform.core.errors import ApiError
from study_platform.persistence.database import get_db
from study_platform.persistence.models import UserSession
from study_platform.services.auth import digest

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    if os.getenv("RUN_DB_TESTS") != "1" or not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("explicit PostgreSQL test database required")
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or {"dbname", "service", "servicefile"} & set(
        url.query
    ):
        pytest.fail("isolated *_test database required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(connection, join_transaction_mode="create_savepoint")
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db

        @app.exception_handler(ApiError)
        async def api_error(request, exc):
            from fastapi.responses import JSONResponse

            return JSONResponse({"code": exc.code}, status_code=exc.status_code)

        @app.post("/protected")
        def protected(user=__import__("fastapi").Depends(get_current_user)):
            return {"id": str(user.id)}

        with TestClient(app) as result:
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


def test_login_errors_and_origin(client):
    http, _ = client
    body = {"email": f"{uuid4().hex}@example.test", "password": "safe-password-123"}
    assert http.post("/auth/login", json=body).status_code == 401
    assert (
        http.post(
            "/auth/register",
            json={**body, "display_name": "Tester"},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )


def test_expired_revoked_disabled_and_deleted_sessions(client):
    from datetime import UTC, datetime, timedelta

    from study_platform.persistence.models import User

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
