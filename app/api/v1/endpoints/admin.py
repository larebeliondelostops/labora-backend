from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.admin_dependencies import AdminContext, require_admin_permission
from app.core.auth_dependencies import get_client_ip, get_user_agent
from app.core.database import get_db
from app.schemas.admin import (
    AdminAssignCaseRequest,
    AdminCaseListResponse,
    AdminCaseStatusUpdateRequest,
    AdminStatusResponse,
    DashboardSummaryResponse,
    DocumentReviewRequest,
    ExtractionCorrectionRequest,
    InternalNoteCreateRequest,
    LegalDraftReviewRequest,
    OverrideUnlockRequest,
    ReportApprovalRequest,
    ResolveAiAlertRequest,
    ReviewDecisionRequest,
)
from app.services.admin_service import AdminService

router = APIRouter()


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    request: Request,
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to_date: Annotated[datetime | None, Query(alias="to")] = None,
    assigned_to_me: Annotated[bool, Query(alias="assignedToMe")] = False,
    context: AdminContext = Depends(require_admin_permission("admin.cases.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).dashboard_summary(
        context=context,
        from_date=from_date,
        to_date=to_date,
        assigned_to_me=assigned_to_me,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases", response_model=AdminCaseListResponse)
def list_cases(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    page_size: Annotated[int | None, Query(alias="pageSize", ge=1, le=100)] = None,
    search: str | None = None,
    q: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    stage: str | None = None,
    priority: str | None = None,
    assigned_to: Annotated[str | None, Query(alias="assignedTo")] = None,
    payment_status: Annotated[str | None, Query(alias="paymentStatus")] = None,
    document_status: Annotated[str | None, Query(alias="documentStatus")] = None,
    has_low_confidence_ai: Annotated[bool | None, Query(alias="hasLowConfidenceAi")] = None,
    has_blocking_issue: Annotated[bool | None, Query(alias="hasBlockingIssue")] = None,
    sort: str = "last_activity_desc",
    context: AdminContext = Depends(require_admin_permission("admin.cases.read")),
    db: Session = Depends(get_db),
) -> dict:
    effective_limit = page_size or limit
    return AdminService(db).list_cases(
        context=context,
        page=page,
        limit=effective_limit,
        search=search,
        q=q,
        status_filter=status_filter,
        stage=stage,
        priority=priority,
        assigned_to=assigned_to,
        payment_status=payment_status,
        document_status=document_status,
        has_low_confidence_ai=has_low_confidence_ai,
        has_blocking_issue=has_blocking_issue,
        sort=sort,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}")
def get_case_detail(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.cases.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_case_detail(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/assign", status_code=status.HTTP_200_OK)
def assign_case(
    case_id: str,
    payload: AdminAssignCaseRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.cases.assign")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).assign_case(
        case_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch("/cases/{case_id}/status", response_model=AdminStatusResponse)
def update_case_status(
    case_id: str,
    payload: AdminCaseStatusUpdateRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.cases.update_status")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).update_case_status(
        case_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/notes")
def list_notes(
    case_id: str,
    request: Request,
    note_type: Annotated[str | None, Query(alias="noteType")] = None,
    visibility: str | None = None,
    context: AdminContext = Depends(require_admin_permission("admin.notes.read_internal")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).list_notes(
        case_id,
        note_type=note_type,
        visibility=visibility,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    case_id: str,
    payload: InternalNoteCreateRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.notes.create")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).create_note(
        case_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/documents")
def get_documents(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.documents.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_documents(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/documents/{document_id}/review")
def review_document(
    case_id: str,
    document_id: str,
    payload: DocumentReviewRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.documents.review")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).review_document(
        case_id,
        document_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/extraction")
def get_extraction(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.extraction.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_extraction(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch("/cases/{case_id}/extraction/items/{item_id}")
def correct_extraction_item(
    case_id: str,
    item_id: str,
    payload: ExtractionCorrectionRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.extraction.correct")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).correct_extraction_item(
        case_id,
        item_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/legal-analysis")
def get_legal_analysis(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.analysis.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_legal_analysis(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/legal-analysis/review")
def review_legal_analysis(
    case_id: str,
    payload: ReviewDecisionRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.analysis.review")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).review_legal_analysis(
        case_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/calculations")
def get_calculations(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.calculations.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_calculations(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/calculations/review")
def review_calculations(
    case_id: str,
    payload: ReviewDecisionRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.calculations.review")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).review_calculations(
        case_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/reports")
def get_reports(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.reports.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_reports(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/reports/{report_id}/approve")
def approve_report(
    case_id: str,
    report_id: str,
    payload: ReportApprovalRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.reports.approve")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).approve_report(
        case_id,
        report_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/legal-drafts")
def get_legal_drafts(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.legal_drafts.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_legal_drafts(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/legal-drafts/{draft_id}/review")
def review_legal_draft(
    case_id: str,
    draft_id: str,
    payload: LegalDraftReviewRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.legal_drafts.approve")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).review_legal_draft(
        case_id,
        draft_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/payment-status")
def get_payment_status(
    case_id: str,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.payments.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_payment_status(
        case_id,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/override-unlock")
def override_unlock(
    case_id: str,
    payload: OverrideUnlockRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.payments.override_unlock")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).override_unlock(
        case_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/ai-alerts")
def get_ai_alerts(
    case_id: str,
    _context: AdminContext = Depends(require_admin_permission("admin.analysis.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).get_ai_alerts(case_id)


@router.post("/cases/{case_id}/ai-alerts/{alert_id}/resolve")
def resolve_ai_alert(
    case_id: str,
    alert_id: str,
    payload: ResolveAiAlertRequest,
    request: Request,
    context: AdminContext = Depends(require_admin_permission("admin.cases.read")),
    db: Session = Depends(get_db),
) -> dict:
    return AdminService(db).resolve_ai_alert(
        case_id,
        alert_id,
        payload,
        context=context,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
