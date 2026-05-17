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
from app.schemas.report import (
    ExportDownloadResponse,
    ReportApproveRequest,
    ReportCreateReadyResponse,
    ReportCreateRequest,
    ReportDetailResponse,
    ReportExportRequest,
    ReportExportResponse,
    ReportListResponse,
    ReportRejectRequest,
    ReportVersionsResponse,
)
from app.services.report_service import ReportExportService, ReportService
from app.workers.report_worker import (
    run_report_export_background,
    run_report_generation_background,
)


router = APIRouter()


@router.post("/cases/{case_id}/reports")
def create_report(
    case_id: str,
    payload: ReportCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body, status_code = ReportService(db).create_or_reuse(
        case_id,
        payload,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if body.get("reportId") and status_code == 202:
        if payload.output_mode == "sync":
            ready = ReportService(db).run_generation(
                body["reportId"],
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
                include_sections=payload.include_sections,
            )
            return JSONResponse(status_code=201, content=ready)
        _enqueue_report_generation(
            background_tasks,
            db=db,
            report_id=body["reportId"],
            user_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
            include_sections=payload.include_sections,
        )
    return JSONResponse(status_code=status_code, content=body)


@router.get(
    "/cases/{case_id}/reports",
    response_model=ReportListResponse,
)
def list_reports(
    case_id: str,
    request: Request,
    report_type: Annotated[str | None, Query(alias="reportType")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ReportService(db).list_reports(
        case_id,
        report_type=report_type,
        report_status=status_filter,
        page=page,
        limit=limit,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/reports/{report_id}",
    response_model=ReportDetailResponse,
)
def get_report(
    report_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ReportService(db).get_report(
        report_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/reports/{report_id}/export",
    response_model=ReportExportResponse,
    status_code=202,
)
def export_report(
    report_id: str,
    payload: ReportExportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    ip_address = get_client_ip(request)
    user_agent = get_user_agent(request)
    body = ReportExportService(db).request_export(
        report_id,
        payload,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    _enqueue_report_export(
        background_tasks,
        db=db,
        export_file_id=body["exportFileId"],
        user_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return body


@router.get(
    "/exports/{export_file_id}/download",
    response_model=ExportDownloadResponse,
)
def download_export(
    export_file_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ReportExportService(db).download_url(
        export_file_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/exports/{export_file_id}/file")
def stream_export_file(
    export_file_id: str,
    expires: Annotated[int, Query()],
    token: Annotated[str, Query()],
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return ReportExportService(db).stream_export(
        export_file_id,
        expires=expires,
        token=token,
    )


@router.get(
    "/reports/{report_id}/versions",
    response_model=ReportVersionsResponse,
)
def list_report_versions(
    report_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ReportService(db).list_versions(
        report_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/reports/{report_id}/approve",
    response_model=ReportCreateReadyResponse,
)
def approve_report(
    report_id: str,
    payload: ReportApproveRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ReportService(db).approve(
        report_id,
        review_notes=payload.review_notes,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/reports/{report_id}/reject",
    response_model=ReportCreateReadyResponse,
)
def reject_report(
    report_id: str,
    payload: ReportRejectRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ReportService(db).reject(
        report_id,
        reason=payload.reason,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


def _enqueue_report_generation(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    report_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
    include_sections: list[str],
) -> None:
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.get_bind(),
    )
    background_tasks.add_task(
        run_report_generation_background,
        report_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        include_sections=include_sections,
        session_factory=session_factory,
    )


def _enqueue_report_export(
    background_tasks: BackgroundTasks,
    *,
    db: Session,
    export_file_id: str,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db.get_bind(),
    )
    background_tasks.add_task(
        run_report_export_background,
        export_file_id,
        actor_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        session_factory=session_factory,
    )
