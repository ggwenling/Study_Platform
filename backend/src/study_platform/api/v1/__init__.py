from fastapi import APIRouter

from study_platform.api.v1 import (
    account_recovery,
    auth,
    chat,
    learning,
    library_detail,
    materials,
    model_connections,
    model_usage,
    resources,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router, tags=["authentication"])
router.include_router(account_recovery.router, tags=["authentication"])
router.include_router(resources.router, tags=["workspace"])
router.include_router(materials.router, tags=["materials"])
router.include_router(library_detail.router, tags=["knowledge base detail"])
router.include_router(chat.router, tags=["chat"])
router.include_router(learning.router)
router.include_router(model_usage.router, tags=["account"])
router.include_router(model_connections.router)
