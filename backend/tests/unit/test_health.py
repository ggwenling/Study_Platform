from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from study_platform.core.config import get_settings
from study_platform.core.errors import ApiError
from study_platform.main import create_app


def migration_head() -> str | None:
    backend = Path(__file__).resolve().parents[2]
    script = ScriptDirectory.from_config(Config(str(backend / "alembic.ini")))
    return script.get_current_head()


def test_liveness_does_not_need_database():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_request_id_validation_error_correlation_and_cors(tmp_path):
    app = create_app(configured(tmp_path))

    @app.get("/test-error")
    def test_error():
        raise ApiError(400, "test_error", "Test error")

    with TestClient(app) as client:
        valid_id = "trace:valid-id_1.2"
        response = client.get(
            "/test-error",
            headers={"Origin": "http://testserver", "X-Request-ID": valid_id},
        )
        assert response.headers["x-request-id"] == valid_id
        assert response.json()["request_id"] == valid_id
        assert "x-request-id" in response.headers["access-control-expose-headers"].lower()

        max_length_id = "a" * 128
        max_length_response = client.get("/health/live", headers={"X-Request-ID": max_length_id})
        assert max_length_response.headers["x-request-id"] == max_length_id

        preflight = client.options(
            "/health/live",
            headers={
                "Origin": "http://testserver",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-request-id",
            },
        )
        assert preflight.status_code == 200
        assert "x-request-id" in preflight.headers["access-control-allow-headers"].lower()

        for invalid_id in ("x" * 129, "invalid value"):
            invalid = client.get("/test-error", headers={"X-Request-ID": invalid_id})
            assert invalid.status_code == 400
            assert invalid.headers["x-request-id"] != invalid_id
            assert invalid.json()["request_id"] == invalid.headers["x-request-id"]


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
    connection.scalar.return_value = migration_head()

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


def test_readiness_requires_infrastructure_not_model_keys(monkeypatch, tmp_path):
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
        connection.scalar.return_value = migration_head()
        settings.embedding_api_key = SecretStr("")
        settings.deepseek_api_key = SecretStr("")
        assert client.get("/health/ready").status_code == 200
        settings.embedding_api_key = SecretStr("test")
        settings.auth_jwt_secret = SecretStr("too-short")
        assert client.get("/health/ready").status_code == 503
        settings.auth_jwt_secret = SecretStr("test-signing-key" * 3)
        settings.storage_dir = tmp_path / "file"
        settings.storage_dir.write_text("not a directory")
        assert client.get("/health/ready").status_code == 503
