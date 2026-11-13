from fastapi import APIRouter

from autoescrow.api.auth import router as auth_router
from autoescrow.api.health import router as health_router
from autoescrow.api.users import router as users_router
from autoescrow.api.wallets import router as wallets_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(wallets_router)
