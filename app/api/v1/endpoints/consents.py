from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.auth import DataResponse
from app.schemas.consent import ConsentSubmitRequest, LegalDocumentCreateRequest
from app.services.consent_service import (
    ConsentComplianceService,
    can_role_read_user_consents,
)

router = APIRouter()
legal_documents_router = APIRouter()
users_router = APIRouter()
admin_router = APIRouter()


@legal_documents_router.get("/current", response_model=DataResponse)
def list_current_legal_documents(
    request: Request,
    types: str | None = Query(default=None),
    locale: str | None = Query(default=None),
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    requested_types = _parse_types(types)
    documents = ConsentComplianceService(db).list_current_documents(
        types=requested_types,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": documents}


@router.post("", response_model=DataResponse, status_code=status.HTTP_201_CREATED)
def register_consents(
    payload: ConsentSubmitRequest,
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, token_payload = context
    data = ConsentComplianceService(db).register_consents(
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        idempotency_key=idempotency_key,
        session_id=token_payload.get("sid"),
    )
    return {"data": data}


@users_router.get("/me/consents/status", response_model=DataResponse)
def get_my_consent_status(
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    status_payload = ConsentComplianceService(db).get_status(user.id)
    return {"data": status_payload.model_dump(by_alias=True)}


@users_router.get("/me/consents", response_model=DataResponse)
def get_my_consent_history(
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    history = ConsentComplianceService(db).list_user_history(user.id)
    return {"data": history}


@users_router.get("/me/permissions/document-upload", response_model=DataResponse)
def get_my_document_upload_permission(
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    permission = ConsentComplianceService(db).can_upload_documents(user.id)
    return {"data": permission.model_dump(by_alias=True)}


@admin_router.post("/legal-documents", response_model=DataResponse, status_code=201)
def create_legal_document(
    payload: LegalDocumentCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    document = ConsentComplianceService(db).create_legal_document(
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": document}


@admin_router.post("/legal-documents/{document_id}/activate", response_model=DataResponse)
def activate_legal_document(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    document = ConsentComplianceService(db).activate_legal_document(
        document_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": document}


@admin_router.get("/users/{user_id}/consents", response_model=DataResponse)
def list_user_consents_for_admin(
    user_id: str,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    if not can_role_read_user_consents(user):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="No tienes permisos para consultar consentimientos de usuarios.",
        )
    history = ConsentComplianceService(db).list_user_history(user_id)
    return {"data": history}


def _parse_types(types: str | None) -> list[str] | None:
    if not types:
        return None
    return [
        consent_type.strip()
        for consent_type in types.split(",")
        if consent_type.strip()
    ]
