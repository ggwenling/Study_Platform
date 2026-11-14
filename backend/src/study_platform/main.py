from contextlib import asynccontextmanager

from fastapi import FastAPI

from study_platform.api.health import router as health_router
from study_platform.api.v1 import router as v1_router
from study_platform.core.config import Settings, get_settings
from study_platform.persistence.session import create_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = create_engine(settings)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="Study Platform API", version="0.1.0", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(v1_router)
    return app
