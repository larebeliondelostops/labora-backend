from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.document_precheck import (
    DocumentPrecheckListResponse,
    DocumentPrecheckStartRequest,
    ManualReviewRequest,
    OcrPreviewRequest,
    OcrPreviewStartResponse,
)
from app.services.document_precheck_service import DocumentPrecheckService


router = APIRouter()
admin_router = APIRouter()


@router.post(
    "/cases/{case_id}/document-precheck",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_document_precheck(
    case_id: str,
    payload: DocumentPrecheckStartRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentPrecheckService(db).start_precheck(
        case_id,
        document_id=payload.document_id,
        force=payload.force,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/document-precheck",
    response_model=DocumentPrecheckListResponse,
)
def list_document_prechecks(
    case_id: str,
    request: Request,
    document_id: Annotated[str | None, Query(alias="documentId")] = None,
    latest: bool = True,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentPrecheckService(db).list_prechecks(
        case_id,
        document_id=document_id,
        latest=latest,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/document-precheck/{precheck_id}")
def get_document_precheck(
    case_id: str,
    precheck_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentPrecheckService(db).get_precheck(
        case_id,
        precheck_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/documents/{document_id}/ocr-preview",
    response_model=OcrPreviewStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_ocr_preview(
    document_id: str,
    payload: OcrPreviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentPrecheckService(db).create_ocr_preview(
        document_id,
        max_pages=payload.max_pages,
        include_text_preview=payload.include_text_preview,
        force=payload.force,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/documents/{document_id}/ocr-preview")
def get_ocr_preview(
    document_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentPrecheckService(db).get_ocr_preview(
        document_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.post("/document-precheck/{precheck_id}/review")
def review_document_precheck(
    precheck_id: str,
    payload: ManualReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DocumentPrecheckService(db).manual_review(
        precheck_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
