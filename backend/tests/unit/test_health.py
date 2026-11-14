from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from study_platform.main import create_app


def test_liveness_does_not_need_database():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_checks_migrated_database(monkeypatch):
    connection = AsyncMock()

    @asynccontextmanager
    async def connect():
        yield connection

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())
    monkeypatch.setattr("study_platform.main.create_engine", lambda settings: engine)
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert "study.users" in str(connection.execute.call_args.args[0])


def test_readiness_failure_does_not_expose_connection_details(monkeypatch):
    @asynccontextmanager
    async def connect():
        raise OperationalError("sensitive connection details", {}, Exception("database down"))
        yield  # pragma: no cover

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())
    monkeypatch.setattr("study_platform.main.create_engine", lambda settings: engine)
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
