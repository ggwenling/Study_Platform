from pathlib import Path
from tempfile import TemporaryFile

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix="/health", tags=["health"])


def expected_schema_revision() -> str:
    backend = Path(__file__).resolve().parents[3]
    script = ScriptDirectory.from_config(Config(str(backend / "alembic.ini")))
    return script.get_current_head() or ""


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    if len(settings.auth_jwt_secret.get_secret_value().encode()) < 32:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    try:
        async with request.app.state.engine.connect() as connection:
            revision = await connection.scalar(
                text("SELECT version_num FROM study.alembic_version")
            )
            await connection.execute(text("SELECT 1 FROM study.knowledge_bases LIMIT 0"))
        expected = expected_schema_revision()
        if not revision or revision != expected:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=settings.storage_dir) as probe:
            probe.write(b"probe")
            probe.flush()
    except (SQLAlchemyError, OSError):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready"})
