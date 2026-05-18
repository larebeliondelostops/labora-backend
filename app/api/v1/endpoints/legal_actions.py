from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.legal_action import (
    AdminLegalDraftsResponse,
    DraftCreateRequest,
    DraftCreateResponse,
    DraftDetailResponse,
    DraftExportRequest,
    DraftExportResponse,
    DraftExportsResponse,
    DraftUpdateRequest,
    DraftUpdateResponse,
    ExportDownloadResponse,
    LegalActionCreateRequest,
    LegalActionListResponse,
    LegalActionResponse,
    LegalActionsAvailableResponse,
    QualityCheckResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    SectionRegenerateRequest,
    SectionRegenerateResponse,
    SubmitReviewRequest,
    SubmitReviewResponse,
)
from app.services.legal_action_service import LegalActionService
from app.workers.legal_action_worker import (
    run_draft_export_background,
    run_draft_quality_check_background,
    run_draft_section_regeneration_background,
    run_legal_draft_generation_background,
)


router = APIRouter()
admin_router = APIRouter()


@router.get(
    "/cases/{case_id}/legal-actions/available",
    response_model=LegalActionsAvailableResponse,
)
def available_legal_actions(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).available_actions(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/legal-actions")
def create_legal_action(
    case_id: str,
    payload: LegalActionCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    body, status_code = LegalActionService(db).create_action(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return JSONResponse(status_code=status_code, content=_json_ready(body))


@router.get(
    "/cases/{case_id}/legal-actions",
    response_model=LegalActionListResponse,
)
def list_legal_actions(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).list_actions(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/legal-actions/{legal_action_id}",
    response_model=LegalActionResponse,
)
def get_legal_action(
    legal_action_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).get_action(
        legal_action_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/legal-actions/{legal_action_id}/drafts", response_model=DraftCreateResponse)
def create_draft(
    legal_action_id: str,
    payload: DraftCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body, status_code = LegalActionService(db).create_draft(
        legal_action_id,
        payload,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if body.get("jobId") and status_code == 202:
        _enqueue_legal_draft_generation(
            background_tasks,
            db=db,
            job_id=body["jobId"],
            user_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return JSONResponse(status_code=status_code, content=_json_ready(body))


@router.get("/drafts/{draft_id}", response_model=DraftDetailResponse)
def get_draft(
    draft_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).get_draft(
        draft_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch("/drafts/{draft_id}", response_model=DraftUpdateResponse)
def update_draft(
    draft_id: str,
    payload: DraftUpdateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).update_draft(
        draft_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/drafts/{draft_id}/sections/{section_id}/regenerate",
    response_model=SectionRegenerateResponse,
    status_code=202,
)
def regenerate_section(
    draft_id: str,
    section_id: str,
    payload: SectionRegenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body = LegalActionService(db).request_section_regeneration(
        draft_id,
        section_id,
        payload,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    _enqueue_section_regeneration(
        background_tasks,
        db=db,
        job_id=body["jobId"],
        user_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return body


@router.post(
    "/drafts/{draft_id}/quality-check",
    response_model=QualityCheckResponse,
    status_code=202,
)
def quality_check(
    draft_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body = LegalActionService(db).request_quality_check(
        draft_id,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    _enqueue_quality_check(
        background_tasks,
        db=db,
        job_id=body["jobId"],
        user_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return body


@router.post(
    "/drafts/{draft_id}/export",
    response_model=DraftExportResponse,
    status_code=202,
)
def export_draft(
    draft_id: str,
    payload: DraftExportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body = LegalActionService(db).request_export(
        draft_id,
        payload,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    _enqueue_export(
        background_tasks,
        db=db,
        job_id=body["jobId"],
        user_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return body


@router.get("/drafts/{draft_id}/exports", response_model=DraftExportsResponse)
def list_draft_exports(
    draft_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).list_exports(
        draft_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/draft-exports/{export_id}/download", response_model=ExportDownloadResponse)
def download_draft_export(
    export_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).download_export_url(
        export_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/draft-exports/{export_id}/file")
def stream_draft_export(
    export_id: str,
    expires: Annotated[int, Query()],
    token: Annotated[str, Query()],
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return LegalActionService(db).stream_export(export_id, expires=expires, token=token)


@router.post("/drafts/{draft_id}/submit-review", response_model=SubmitReviewResponse)
def submit_review(
    draft_id: str,
    payload: SubmitReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).submit_review(
        draft_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.get("/legal-drafts", response_model=AdminLegalDraftsResponse)
def admin_list_legal_drafts(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    case_id: Annotated[str | None, Query(alias="case_id")] = None,
    action_type: Annotated[str | None, Query(alias="action_type")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).admin_list_drafts(
        status_filter=status_filter,
        case_id=case_id,
        action_type=action_type,
        page=page,
        page_size=page_size,
        user=user,
    )


@admin_router.post(
    "/legal-drafts/{draft_id}/review-decision",
    response_model=ReviewDecisionResponse,
)
def admin_review_decision(
    draft_id: str,
    payload: ReviewDecisionRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return LegalActionService(db).review_decision(
        draft_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


def _enqueue_legal_draft_generation(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    job_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    session_factory = _session_factory(db)
    background_tasks.add_task(
        run_legal_draft_generation_background,
        job_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        session_factory=session_factory,
    )


def _enqueue_section_regeneration(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    job_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    session_factory = _session_factory(db)
    background_tasks.add_task(
        run_draft_section_regeneration_background,
        job_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        session_factory=session_factory,
    )


def _enqueue_quality_check(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    job_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    session_factory = _session_factory(db)
    background_tasks.add_task(
        run_draft_quality_check_background,
        job_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        session_factory=session_factory,
    )


def _enqueue_export(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    job_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    session_factory = _session_factory(db)
    background_tasks.add_task(
        run_draft_export_background,
        job_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        session_factory=session_factory,
    )


def _session_factory(db: Session):
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.get_bind(),
    )


def _json_ready(value):
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
