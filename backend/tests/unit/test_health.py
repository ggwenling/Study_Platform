from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from study_platform.core.config import get_settings
from study_platform.main import create_app


def test_liveness_does_not_need_database():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def configured(tmp_path):
    settings = get_settings().model_copy(
        update={
            "storage_dir": tmp_path,
            "auth_jwt_secret": SecretStr("test-signing-key" * 3),
            "deepseek_api_key": SecretStr("test"),
            "embedding_api_key": SecretStr("test"),
        }
    )
    return settings


def test_readiness_checks_migrated_database(monkeypatch, tmp_path):
    settings = configured(tmp_path)
    connection = AsyncMock()
    connection.scalar.return_value = "0007_learning_verdict"

    @asynccontextmanager
    async def connect():
        yield connection

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())
    monkeypatch.setattr("study_platform.main.create_engine", lambda settings: engine)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert "study.knowledge_bases" in str(connection.execute.call_args.args[0])
    assert "study.alembic_version" in str(connection.scalar.call_args.args[0])


def test_readiness_failure_does_not_expose_connection_details(monkeypatch, tmp_path):
    settings = configured(tmp_path)

    @asynccontextmanager
    async def connect():
        raise OperationalError("sensitive connection details", {}, Exception("database down"))
        yield  # pragma: no cover

    engine = SimpleNamespace(connect=connect, dispose=AsyncMock())
    monkeypatch.setattr("study_platform.main.create_engine", lambda settings: engine)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_readiness_requires_keys_revision_and_writable_storage(monkeypatch, tmp_path):
    settings = configured(tmp_path)
    connection = AsyncMock()
    connection.scalar.return_value = "0001_initial_schema"

    @asynccontextmanager
    async def connect():
        yield connection

    monkeypatch.setattr(
        "study_platform.main.create_engine",
        lambda _: SimpleNamespace(connect=connect, dispose=AsyncMock()),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 503
        connection.scalar.return_value = "0007_learning_verdict"
        settings.embedding_api_key = SecretStr("")
        assert client.get("/health/ready").status_code == 503
        settings.embedding_api_key = SecretStr("test")
        settings.auth_jwt_secret = SecretStr("too-short")
        assert client.get("/health/ready").status_code == 503
        settings.auth_jwt_secret = SecretStr("test-signing-key" * 3)
        settings.storage_dir = tmp_path / "file"
        settings.storage_dir.write_text("not a directory")
        assert client.get("/health/ready").status_code == 503
