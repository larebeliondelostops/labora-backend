from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.case import (
    AdminAssignCaseRequest,
    AdminCaseTagRequest,
    AdminCaseDetailResponse,
    CaseCloseRequest,
    CaseCloseResponse,
    CaseCreateRequest,
    CaseCreateResponse,
    CaseDetailResponse,
    CaseHistoryResponse,
    CaseListResponse,
    CaseSubmitResponse,
    CaseUpdateRequest,
    CaseUpdateResponse,
    InternalAiSuggestionRequest,
    InternalCaseStatusUpdateRequest,
    InternalCaseStatusUpdateResponse,
)
from app.services.case_service import CaseService

router = APIRouter()
admin_router = APIRouter()
internal_router = APIRouter()


@router.post("", response_model=CaseCreateResponse, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).create_case(
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("", response_model=CaseListResponse)
def list_cases(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).list_my_cases(
        user=user,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get("/{case_id}", response_model=CaseDetailResponse)
def get_case(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).get_case(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch("/{case_id}", response_model=CaseUpdateResponse)
def update_case(
    case_id: str,
    payload: CaseUpdateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseService(db).update_case(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/{case_id}/submit", response_model=CaseSubmitResponse)
def submit_case(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).submit_case(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/{case_id}/history", response_model=CaseHistoryResponse)
def get_case_history(
    case_id: str,
    request: Request,
    sort: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).get_history(
        case_id,
        user=user,
        sort=sort,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/{case_id}/close", response_model=CaseCloseResponse)
def close_case(
    case_id: str,
    payload: CaseCloseRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseService(db).close_case(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@internal_router.post("/{case_id}/status", response_model=InternalCaseStatusUpdateResponse)
def update_case_status_internal(
    case_id: str,
    payload: InternalCaseStatusUpdateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseService(db).update_status_internal(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@internal_router.post("/{case_id}/ai-suggestion")
def apply_case_ai_suggestion(
    case_id: str,
    payload: InternalAiSuggestionRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseService(db).apply_ai_suggestion(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.get("", response_model=CaseListResponse)
def list_admin_cases(
    request: Request,
    q: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    case_type_requested: Annotated[
        str | None,
        Query(alias="caseTypeRequested"),
    ] = None,
    situation_type: Annotated[str | None, Query(alias="situationType")] = None,
    created_from: Annotated[datetime | None, Query(alias="createdFrom")] = None,
    created_to: Annotated[datetime | None, Query(alias="createdTo")] = None,
    updated_from: Annotated[datetime | None, Query(alias="updatedFrom")] = None,
    updated_to: Annotated[datetime | None, Query(alias="updatedTo")] = None,
    assigned_to: Annotated[str | None, Query(alias="assignedTo")] = None,
    tag: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 50,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).list_admin_cases(
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        q=q,
        status_filter=status_filter,
        case_type_requested=case_type_requested,
        situation_type=situation_type,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
        assigned_to=assigned_to,
        tag=tag,
        page=page,
        page_size=page_size,
    )


@admin_router.get("/{case_id}", response_model=AdminCaseDetailResponse)
def get_admin_case(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return CaseService(db).get_admin_case(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.post("/{case_id}/assign")
def assign_case(
    case_id: str,
    payload: AdminAssignCaseRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseService(db).assign_case(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.post("/{case_id}/tags")
def add_case_tag(
    case_id: str,
    payload: AdminCaseTagRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return CaseService(db).add_tag(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
