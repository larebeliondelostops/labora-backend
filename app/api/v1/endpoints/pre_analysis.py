from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.pre_analysis import (
    AdminPreAnalysisListResponse,
    PreAnalysisCreateRequest,
    PreAnalysisResultResponse,
    PreAnalysisRetryRequest,
    PreAnalysisReviewRequest,
    PreAnalysisStatusResponse,
)
from app.services.pre_analysis_service import PreAnalysisService


router = APIRouter()
admin_router = APIRouter()


@router.post("/cases/{case_id}/pre-analysis")
def create_pre_analysis(
    case_id: str,
    payload: PreAnalysisCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    body, status_code = PreAnalysisService(db).create_or_reuse(
        case_id,
        force_regenerate=payload.force_regenerate,
        source=payload.source,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return JSONResponse(status_code=status_code, content=body)


@router.get(
    "/cases/{case_id}/pre-analysis",
    response_model=PreAnalysisResultResponse,
)
def get_pre_analysis(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PreAnalysisService(db).get_user_facing_result(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/pre-analysis/status",
    response_model=PreAnalysisStatusResponse,
)
def get_pre_analysis_status(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PreAnalysisService(db).get_status(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/pre-analysis/retry")
def retry_pre_analysis(
    case_id: str,
    payload: PreAnalysisRetryRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    body, status_code = PreAnalysisService(db).retry(
        case_id,
        reason=payload.reason,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return JSONResponse(status_code=status_code, content=body)


@admin_router.get(
    "/pre-analysis",
    response_model=AdminPreAnalysisListResponse,
)
def list_admin_pre_analysis(
    request: Request,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    case_id: Annotated[str | None, Query(alias="caseId")] = None,
    user_id: Annotated[str | None, Query(alias="userId")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PreAnalysisService(db).list_admin(
        status_filter=status_filter,
        case_id=case_id,
        user_id=user_id,
        page=page,
        page_size=page_size,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.patch(
    "/pre-analysis/{pre_analysis_id}/review",
    response_model=PreAnalysisResultResponse,
)
def review_pre_analysis(
    pre_analysis_id: str,
    payload: PreAnalysisReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PreAnalysisService(db).review(
        pre_analysis_id,
        review_status=payload.status,
        review_notes=payload.review_notes,
        traffic_light=payload.traffic_light,
        viability_level=payload.viability_level,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
