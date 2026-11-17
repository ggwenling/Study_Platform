from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from study_platform.api.health import router as health_router
from study_platform.api.v1 import router as v1_router
from study_platform.core.config import Settings, get_settings
from study_platform.core.errors import ApiError, api_error_handler
from study_platform.persistence.session import create_engine
from study_platform.services.auth import validate_cookie_security


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    validate_cookie_security(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = create_engine(settings)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="Study Platform API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "Idempotency-Key"])
    app.add_exception_handler(ApiError, api_error_handler)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return await api_error_handler(request, ApiError(
            422, "validation_error", "请求字段缺失或格式不正确"))

    app.include_router(health_router)
    app.include_router(v1_router)
    return app
