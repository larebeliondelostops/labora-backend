from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, public, users

api_router = APIRouter()

api_router.include_router(
    health.router,
    prefix="/health",
    tags=["health"],
)
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"],
)
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["users"],
)
api_router.include_router(
    public.router,
    prefix="/public",
    tags=["public"],
)
api_router.include_router(
    public.leads_router,
    prefix="/leads",
    tags=["leads"],
)
