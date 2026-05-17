from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.case_result import (
    CaseResultResponse,
    CaseResultUpdateRequest,
    ResultApproveRequest,
    ResultGenerateRequest,
    ResultGenerateResponse,
    ResultRejectRequest,
    ResultStatusResponse,
)
from app.services.case_result_service import CaseResultService


router = APIRouter()


@router.get(
    "/cases/{case_id}/result",
    response_model=CaseResultResponse,
)
def get_case_result(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseResultService(db).get_result(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/result/status",
    response_model=ResultStatusResponse,
)
def get_case_result_status(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseResultService(db).get_status(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/result/generate",
    response_model=ResultGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_case_result(
    case_id: str,
    request: Request,
    payload: ResultGenerateRequest | None = Body(default=None),
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    payload = payload or ResultGenerateRequest()
    return CaseResultService(db).generate(
        case_id,
        force_regenerate=payload.force_regenerate,
        reason=payload.reason,
        source_analysis_id=payload.source_analysis_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch(
    "/cases/{case_id}/result/{result_id}",
    response_model=CaseResultResponse,
)
def update_case_result(
    case_id: str,
    result_id: str,
    payload: CaseResultUpdateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseResultService(db).update_result(
        case_id,
        result_id,
        payload=payload.model_dump(exclude_unset=True),
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/result/{result_id}/approve",
    response_model=CaseResultResponse,
)
def approve_case_result(
    case_id: str,
    result_id: str,
    request: Request,
    payload: ResultApproveRequest | None = Body(default=None),
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    payload = payload or ResultApproveRequest()
    return CaseResultService(db).approve_result(
        case_id,
        result_id,
        comment=payload.comment,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/result/{result_id}/reject",
    response_model=CaseResultResponse,
)
def reject_case_result(
    case_id: str,
    result_id: str,
    payload: ResultRejectRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseResultService(db).reject_result(
        case_id,
        result_id,
        reason=payload.reason,
        send_to_regeneration=payload.send_to_regeneration,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/result/{result_id}/viewed")
def mark_case_result_viewed(
    case_id: str,
    result_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseResultService(db).mark_viewed(
        case_id,
        result_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
