from fastapi import APIRouter

from app.api.routes.configurations import router as configurations_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.feedback import router as feedback_router
from app.api.routes.health import router as health_router
from app.api.routes.webhooks import router as webhooks_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(webhooks_router)
api_router.include_router(feedback_router)
api_router.include_router(configurations_router)
api_router.include_router(dashboard_router)
