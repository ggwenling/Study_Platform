"""Bearer, refresh, Google and recovery checks in an isolated test database."""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
import sqlalchemy as sa
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from study_platform.api.v1 import account_recovery
from study_platform.api.v1.auth import get_current_user
from study_platform.core.config import Settings
from study_platform.main import create_app
from study_platform.persistence.database import get_db
from study_platform.persistence.models import User, UserSession
from study_platform.persistence.models.identity import PasswordReset
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
    settings = Settings(_env_file=None, database_url=url.render_as_string(hide_password=False),
                        auth_jwt_secret="auth-test-key" * 3, auth_public_url="http://testserver")
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


def test_bearer_validation_refresh_and_revocation(client):
    http, db = client
    body = {
        "email": f"{uuid4().hex}@example.test",
        "password": "safe-password-123",
        "display_name": "Tester",
    }
    response = http.post("/auth/register", json=body)
    assert response.status_code == 201
    first = response.json()["user"]["id"]
    access = response.json()["access_token"]
    cookies = response.headers.get_list("set-cookie")
    assert any("study_refresh=" in c and "HttpOnly" in c and "Path=/api/v1/auth" in c for c in cookies)
    assert any("study_csrf=" in c and "HttpOnly" not in c for c in cookies)
    token = http.cookies["study_refresh"]
    saved = db.scalar(sa.select(UserSession).where(UserSession.refresh_token_hash == digest(token)))
    assert saved and saved.refresh_token_hash != token
    assert http.get("/me").status_code == 401
    headers = {"Authorization": "Bearer " + access}
    assert http.get("/me", headers=headers).json()["id"] == first
    assert http.post("/protected", headers=headers).status_code == 200
    claims = jwt.decode(access, options={"verify_signature": False})
    for key, updates in [("a" * 32, {}), ("auth-test-key" * 3, {"exp": 1}),
                         ("auth-test-key" * 3, {"aud": "wrong"})]:
        invalid = jwt.encode({**claims, **updates}, key, algorithm="HS256", headers={"typ": "at+jwt"})
        assert http.get("/me", headers={"Authorization": "Bearer " + invalid}).status_code == 401
    assert http.post("/auth/refresh").status_code == 403
    csrf = {"X-CSRF-Token": http.cookies["study_csrf"]}
    assert http.post("/auth/refresh", headers={**csrf, "Origin": "https://evil.test"}).status_code == 403
    assert http.post("/auth/refresh", headers=csrf).json()["user"]["id"] == first
    assert http.post("/auth/logout", headers=csrf).status_code == 204
    assert http.get("/me", headers=headers).status_code == 401
    assert http.post("/auth/refresh", headers=csrf).status_code == 401


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
    assert response.json()["user"] == registered.json()["user"]
    assert "study_refresh" in response.cookies and "study_csrf" in response.cookies
    assert db.scalar(sessions) == original_count + 1
    assert http.get("/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}).json() == registered.json()["user"]


def test_expired_revoked_disabled_and_deleted_sessions(client):
    http, db = client
    body = {
        "email": f"{uuid4().hex}@example.test",
        "password": "safe-password-123",
        "display_name": "Tester",
    }
    response = http.post("/auth/register", json=body)
    assert response.status_code == 201
    http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    assert http.get("/me").status_code == 200
    session = db.scalar(
        sa.select(UserSession).where(
            UserSession.refresh_token_hash == digest(http.cookies["study_refresh"])
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


def test_google_signin_and_verified_account_link(client, monkeypatch):
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.asymmetric import rsa

    http, _ = client
    settings = http.app.state.settings
    settings.google_client_id = "google-client-test"
    from pydantic import SecretStr
    settings.google_client_secret = SecretStr("google-test-secret")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    profile = {"email": f"{uuid4().hex}@example.test", "sub": uuid4().hex, "nonce": "", "name": "Google tester"}

    def fake_exchange(url, *, data, timeout):
        assert url == "https://oauth2.googleapis.com/token"
        expected = account_recovery.base64.urlsafe_b64encode(
            account_recovery.hashlib.sha256(data["code_verifier"].encode()).digest()
        ).decode().rstrip("=")
        assert expected == profile["challenge"]
        claims = {**profile, "email_verified": True, "iss": account_recovery.GOOGLE,
                  "aud": settings.google_client_id, "iat": datetime.now(UTC),
                  "exp": datetime.now(UTC) + timedelta(minutes=5)}
        return httpx.Response(200, json={"id_token": jwt.encode(claims, private_key, algorithm="RS256")},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(account_recovery.httpx, "post", fake_exchange)
    monkeypatch.setattr(account_recovery.GOOGLE_KEYS, "get_signing_key_from_jwt",
                        lambda _: SimpleNamespace(key=private_key.public_key()))

    def callback(wrong_state=False):
        start = http.get("/auth/google/start", params={"next": "/history"}, follow_redirects=False)
        query = parse_qs(urlsplit(start.headers["location"]).query)
        profile.update(nonce=query["nonce"][0], challenge=query["code_challenge"][0])
        return http.get("/auth/google/callback", params={
            "code": "test-code", "state": "wrong" if wrong_state else query["state"][0],
        }, follow_redirects=False)

    assert callback(wrong_state=True).headers["location"].endswith("auth=google_error")
    assert callback().headers["location"] == "http://testserver/history"
    csrf = {"X-CSRF-Token": http.cookies["study_csrf"]}
    assert http.post("/auth/refresh", headers=csrf).json()["user"]["email"] == profile["email"]
    assert http.post("/auth/logout", headers=csrf).status_code == 204

    profile.update(email=f"{uuid4().hex}@example.test", sub=uuid4().hex)
    body = {"email": profile["email"], "password": "safe-password-123", "display_name": "Existing"}
    account = http.post("/auth/register", json=body).json()["user"]
    assert http.post("/auth/logout", headers={"X-CSRF-Token": http.cookies["study_csrf"]}).status_code == 204
    assert callback().headers["location"].endswith("auth=google_link")
    assert http.post("/auth/google/link", json={"password": "incorrect-password"}).status_code == 401
    assert http.post("/auth/google/link", json={"password": body["password"]}).json()["user"] == account
    assert callback().headers["location"] == "http://testserver/history"


def test_reset_email_token_expiry_and_reuse(client):
    import socketserver
    from threading import Thread

    http, db = client
    settings = http.app.state.settings
    settings.smtp_from = "test@example.test"
    settings.smtp_starttls = False
    sent = []

    class Inbox(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 local SMTP inbox\r\n")
            for line in self.rfile:
                command = line.split(maxsplit=1)[0].upper() if line.strip() else b""
                if command == b"DATA":
                    self.wfile.write(b"354 Send message\r\n")
                    message = b"".join(iter(self.rfile.readline, b".\r\n"))
                    sent.append(message)
                elif command == b"QUIT":
                    self.wfile.write(b"221 Bye\r\n")
                    return
                self.wfile.write(b"250 OK\r\n")

    def delivered_token(index):
        from email import policy
        from email.parser import BytesParser

        message = BytesParser(policy=policy.default).parsebytes(sent[index])
        assert message["To"] == body["email"]
        return message.get_content().split("#reset=")[1].split()[0]

    body = {"email": f"{uuid4().hex}@example.test", "password": "safe-password-123", "display_name": "Tester"}
    access = http.post("/auth/register", json=body).json()["access_token"]
    with socketserver.TCPServer(("127.0.0.1", 0), Inbox) as smtp:
        settings.smtp_host, settings.smtp_port = smtp.server_address
        thread = Thread(target=smtp.serve_forever, daemon=True)
        thread.start()
        try:
            first = http.post("/auth/password/forgot", json={"email": body["email"]})
            absent = http.post("/auth/password/forgot", json={"email": f"{uuid4().hex}@example.test"})
            assert first.status_code == absent.status_code == 202
            assert first.json() == absent.json()
            assert http.post("/auth/password/forgot", json={"email": body["email"]}).status_code == 202
            assert len(sent) == 1
            token = delivered_token(0)
            row = db.get(PasswordReset, digest(token))
            row.created_at -= timedelta(minutes=31)
            db.commit()
            changed = {"token": token, "password": "new-password-123"}
            assert http.post("/auth/password/reset", json=changed).status_code == 400
            assert http.post("/auth/password/forgot", json={"email": body["email"]}).status_code == 202
            changed["token"] = delivered_token(-1)
        finally:
            smtp.shutdown()
            thread.join()
    assert http.post("/auth/password/reset", json=changed).status_code == 204
    assert http.post("/auth/password/reset", json=changed).status_code == 400
    assert http.get("/me", headers={"Authorization": "Bearer " + access}).status_code == 401
    assert http.post("/auth/login", json=body).status_code == 401
    assert http.post("/auth/login", json={**body, "password": changed["password"]}).status_code == 200


def test_bearer_required_for_upload_and_private_files(client, monkeypatch, tmp_path):
    from uuid import UUID

    from study_platform.core.config import get_settings
    from study_platform.services.storage import page_path

    http, _ = client
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    account = http.post("/auth/register", json={
        "email": f"{uuid4().hex}@example.test", "password": "safe-password-123", "display_name": "Owner",
    }).json()
    bearer = {"Authorization": "Bearer " + account["access_token"]}
    kb = http.post("/knowledge-bases", headers=bearer, json={"name": "Notes"}).json()
    files = {"file": ("source.txt", b"test document", "text/plain")}
    path = f'/knowledge-bases/{kb["id"]}/materials'
    assert http.post(path, files=files).status_code == 401
    upload = http.post(path, headers=bearer, files=files)
    assert upload.status_code == 202
    version_id = UUID(upload.json()["version_id"])
    page_path(version_id, 1).write_bytes(b"test image")
    for suffix, content in (("content", b"test document"), ("pages/1", b"test image")):
        url = f"/material-versions/{version_id}/{suffix}"
        assert http.get(url).status_code == 401
        assert http.get(url, headers=bearer).content == content
