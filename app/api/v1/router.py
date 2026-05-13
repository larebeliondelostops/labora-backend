from fastapi import APIRouter

from app.api.v1.endpoints import auth, consents, health, public, users

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
    consents.legal_documents_router,
    prefix="/legal-documents",
    tags=["legal-documents"],
)
api_router.include_router(
    consents.router,
    prefix="/consents",
    tags=["consents"],
)
api_router.include_router(
    consents.users_router,
    prefix="/users",
    tags=["consents"],
)
api_router.include_router(
    consents.admin_router,
    prefix="/admin",
    tags=["admin-consents"],
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
