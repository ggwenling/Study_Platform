"""Model usage persistence and per-user summary isolation."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from study_platform.core.config import Settings
from study_platform.main import create_app
from study_platform.persistence.models import ModelCallRecord, User
from study_platform.services.auth import hasher


@pytest.fixture
def usage_db(monkeypatch):
    raw_url = os.getenv("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a PostgreSQL database ending in _test")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    settings = Settings(
        _env_file=None,
        database_url=url.render_as_string(hide_password=False),
        auth_jwt_secret="model-usage-test-secret-key-0123456789",
        auth_public_url="http://testserver",
    )
    app = create_app(settings)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            factory = sessionmaker(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            monkeypatch.setattr("study_platform.persistence.database.session_factory", lambda: factory)
            with TestClient(app, headers={"Origin": "http://testserver"}) as client:
                emails = [f"usage-{uuid4().hex}@example.com" for _ in range(2)]
                with factory() as db:
                    users = [
                        User(
                            email=email,
                            password_hash=hasher.hash("Password123!"),
                            display_name=f"User {idx}",
                        )
                        for idx, email in enumerate(emails)
                    ]
                    db.add_all(users)
                    db.flush()
                    db.add_all(
                        [
                            ModelCallRecord(
                                owner_user_id=users[0].id,
                                purpose="chat",
                                model_name="deepseek-flash",
                                used_fallback=False,
                                usage={"total_tokens": 10},
                                elapsed_ms=Decimal("12.5"),
                                success=True,
                                created_at=datetime.now(UTC),
                            ),
                            ModelCallRecord(
                                owner_user_id=users[0].id,
                                purpose="embedding",
                                model_name="qwen-embed",
                                used_fallback=False,
                                usage={"total_tokens": 4},
                                elapsed_ms=Decimal("3"),
                                success=True,
                                created_at=datetime.now(UTC),
                            ),
                            ModelCallRecord(
                                owner_user_id=users[1].id,
                                purpose="learning_grade",
                                model_name="deepseek-flash",
                                used_fallback=True,
                                usage=None,
                                elapsed_ms=Decimal("80"),
                                success=False,
                                created_at=datetime.now(UTC),
                            ),
                        ]
                    )
                    db.commit()
                    owner_ids = [str(users[0].id), str(users[1].id)]
                tokens = []
                for email in emails:
                    response = client.post(
                        "/api/v1/auth/login",
                        json={"email": email, "password": "Password123!"},
                    )
                    assert response.status_code == 200, response.text
                    tokens.append(response.json()["access_token"])
                yield client, tokens, owner_ids
            transaction.rollback()
    finally:
        engine.dispose()


def test_model_usage_summary_isolated_per_user(usage_db):
    client, tokens, owner_ids = usage_db
    first = client.get(
        "/api/v1/me/model-usage",
        headers={"Authorization": f"Bearer {tokens[0]}"},
    )
    second = client.get(
        "/api/v1/me/model-usage",
        headers={"Authorization": f"Bearer {tokens[1]}"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    body_a = first.json()
    body_b = second.json()
    assert body_a["totals"]["calls"] == 2
    assert body_b["totals"]["calls"] == 1
    assert body_b["totals"]["failures"] == 1
    assert body_b["totals"]["fallback_calls"] == 1
    purposes = {row["key"] for row in body_a["by_purpose"]}
    assert purposes == {"chat", "embedding"}
    assert {row["key"] for row in body_b["by_purpose"]} == {"learning_grade"}
    assert first.headers.get("x-request-id")
    assert owner_ids


def test_record_model_call_failure_does_not_raise(monkeypatch):
    from study_platform.services.model_usage import ModelUsageContext, record_model_call

    class BrokenSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add(self, *_args, **_kwargs):
            raise RuntimeError("db down")

        def commit(self):
            pass

    monkeypatch.setattr(
        "study_platform.services.model_usage.session_factory",
        lambda: (lambda: BrokenSession()),
    )
    record_model_call(
        ModelUsageContext(uuid4(), "chat"),
        model_name="m",
        used_fallback=False,
        elapsed_ms=1.0,
        success=False,
        usage=None,
    )
