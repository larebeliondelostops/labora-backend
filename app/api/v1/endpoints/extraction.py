from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.extraction import (
    ConfirmExtractionRequest,
    ConfirmExtractionResponse,
    CorrectionsListResponse,
    ExtractionFieldsPatchRequest,
    ExtractionResponse,
    ExtractionRunCreateRequest,
    ExtractionRunStartResponse,
    IgnoreEntityRequest,
    IgnoreEntityResponse,
    IssueResolveRequest,
    IssueResolveResponse,
    IssuesListResponse,
    ManualEmployerCreateRequest,
    ManualLaborPeriodCreateRequest,
    PatchFieldsResponse,
)
from app.services.extraction_service import ExtractionService

router = APIRouter()


@router.get(
    "/cases/{case_id}/extraction",
    response_model=ExtractionResponse,
)
def get_case_extraction(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return ExtractionService(db).get_extraction(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/extraction-runs",
    response_model=ExtractionRunStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_extraction_run(
    case_id: str,
    payload: ExtractionRunCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).start_run(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch(
    "/cases/{case_id}/extraction-fields",
    response_model=PatchFieldsResponse,
)
def update_extraction_fields(
    case_id: str,
    payload: ExtractionFieldsPatchRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).update_fields(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/extraction/employers",
    status_code=status.HTTP_201_CREATED,
)
def create_manual_employer(
    case_id: str,
    payload: ManualEmployerCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).create_employer(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/extraction/labor-periods",
    status_code=status.HTTP_201_CREATED,
)
def create_manual_labor_period(
    case_id: str,
    payload: ManualLaborPeriodCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).create_labor_period(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch(
    "/cases/{case_id}/extraction/entities/{entity_type}/{entity_id}/ignore",
    response_model=IgnoreEntityResponse,
)
def ignore_extraction_entity(
    case_id: str,
    entity_type: str,
    entity_id: str,
    payload: IgnoreEntityRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).ignore_entity(
        case_id,
        entity_type,
        entity_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/confirm-extraction",
    response_model=ConfirmExtractionResponse,
)
def confirm_extraction(
    case_id: str,
    payload: ConfirmExtractionRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).confirm_extraction(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/extraction/corrections",
    response_model=CorrectionsListResponse,
)
def list_corrections(
    case_id: str,
    request: Request,
    entity_type: Annotated[str | None, Query(alias="entityType")] = None,
    entity_id: Annotated[str | None, Query(alias="entityId")] = None,
    field_key: Annotated[str | None, Query(alias="fieldKey")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).list_corrections(
        case_id,
        user=user,
        entity_type=entity_type,
        entity_id=entity_id,
        field_key=field_key,
        page=page,
        limit=limit,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/extraction/issues",
    response_model=IssuesListResponse,
)
def list_extraction_issues(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).list_issues(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch(
    "/cases/{case_id}/extraction/issues/{issue_id}",
    response_model=IssueResolveResponse,
)
def resolve_extraction_issue(
    case_id: str,
    issue_id: str,
    payload: IssueResolveRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ExtractionService(db).resolve_issue(
        case_id,
        issue_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
