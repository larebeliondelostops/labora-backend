from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    analysis,
    auth,
    cases,
    consents,
    documents,
    extraction,
    health,
    legal_actions,
    ocr,
    payments,
    questionnaires,
    reports,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(consents.router, prefix="/consents", tags=["consents"])
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(ocr.router, tags=["ocr"])
api_router.include_router(extraction.router, tags=["extraction"])
api_router.include_router(questionnaires.router, tags=["questionnaires"])
api_router.include_router(analysis.router, tags=["analysis"])
api_router.include_router(reports.router, tags=["reports"])
api_router.include_router(legal_actions.router, tags=["legal-actions"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
