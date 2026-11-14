from tempfile import TemporaryFile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    if (
        len(settings.auth_jwt_secret.get_secret_value().encode()) < 32
        or not settings.deepseek_api_key.get_secret_value()
        or not settings.embedding_api_key.get_secret_value()
    ):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    try:
        async with request.app.state.engine.connect() as connection:
            revision = await connection.scalar(
                text("SELECT version_num FROM study.alembic_version")
            )
            await connection.execute(text("SELECT 1 FROM study.knowledge_bases LIMIT 0"))
        if revision != "0004_project_conversations":
            return JSONResponse({"status": "not_ready"}, status_code=503)
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=settings.storage_dir) as probe:
            probe.write(b"probe")
            probe.flush()
    except (SQLAlchemyError, OSError):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready"})
