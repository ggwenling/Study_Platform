from fastapi import APIRouter

from study_platform.api.v1 import account_recovery, auth, chat, materials, resources

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router, tags=["authentication"])
router.include_router(account_recovery.router, tags=["authentication"])
router.include_router(resources.router, tags=["workspace"])
router.include_router(materials.router, tags=["materials"])
router.include_router(chat.router, tags=["chat"])
