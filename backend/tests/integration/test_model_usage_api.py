"""Model usage persistence and per-user summary isolation."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from study_platform.main import create_app
from study_platform.persistence.database import session_factory
from study_platform.persistence.models import ModelCallRecord, User
from study_platform.services.auth import hasher


@pytest.fixture
def usage_db(monkeypatch):
    app = create_app()
    client = TestClient(app)
    with session_factory()() as db:
        users = [
            User(
                email=f"usage-{idx}@example.com",
                password_hash=hasher.hash("Password123!"),
                display_name=f"User {idx}",
            )
            for idx in range(2)
        ]
        db.add_all(users)
        db.commit()
        for user in users:
            db.refresh(user)
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
    monkeypatch.setenv("AUTH_JWT_SECRET", "x" * 48)
    tokens = []
    for owner_id, email in zip(owner_ids, ["usage-0@example.com", "usage-1@example.com"], strict=True):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123!"},
        )
        assert response.status_code == 200, response.text
        tokens.append(response.json()["access_token"])
    return client, tokens, owner_ids


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