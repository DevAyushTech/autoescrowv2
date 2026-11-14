from fastapi import APIRouter

from autoescrow.api.agents import router as agents_router
from autoescrow.api.agreements import router as agreements_router
from autoescrow.api.auth import router as auth_router
from autoescrow.api.health import router as health_router
from autoescrow.api.jobs import router as jobs_router
from autoescrow.api.ledger import router as ledger_router
from autoescrow.api.marketplace import router as marketplace_router
from autoescrow.api.matching import router as matching_router
from autoescrow.api.users import router as users_router
from autoescrow.api.wallets import router as wallets_router
from autoescrow.api.work import router as work_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(wallets_router)
api_router.include_router(agents_router)
api_router.include_router(marketplace_router)
api_router.include_router(jobs_router)
api_router.include_router(matching_router)
api_router.include_router(agreements_router)
api_router.include_router(ledger_router)
api_router.include_router(work_router)
