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
from app.schemas.professional_review import (
    AiSummaryResponse,
    ApproveReviewRequest,
    AssignReviewRequest,
    AssignReviewResponse,
    AssignmentResponseRequest,
    CancelReviewRequest,
    CreateCommentRequest,
    LawyerCommentDto,
    ProfessionalReviewCreateRequest,
    ProfessionalReviewCreateResponse,
    ProfessionalReviewDetailResponse,
    ProfessionalReviewListResponse,
    ProfessionalReviewPatchRequest,
    RejectReviewRequest,
    RequestClientActionRequest,
    ResolveCommentRequest,
    ReviewedFileCreateRequest,
    ReviewedFileDto,
)
from app.services.professional_review_service import ProfessionalReviewService


router = APIRouter()


@router.post(
    "/cases/{case_id}/professional-review",
    response_model=ProfessionalReviewCreateResponse,
)
def create_professional_review(
    case_id: str,
    payload: ProfessionalReviewCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> JSONResponse:
    user, _token_payload = context
    body, status_code = ProfessionalReviewService(db).create_review(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return JSONResponse(status_code=status_code, content=_json_ready(body))


@router.get(
    "/professional-reviews",
    response_model=ProfessionalReviewListResponse,
)
def list_professional_reviews(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    case_id: Annotated[str | None, Query(alias="caseId")] = None,
    mine: Annotated[bool, Query()] = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).list_reviews(
        status_filter=status_filter,
        case_id=case_id,
        mine=mine,
        page=page,
        page_size=page_size,
        user=user,
    )


@router.get(
    "/professional-reviews/{review_id}",
    response_model=ProfessionalReviewDetailResponse,
)
def get_professional_review(
    review_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).get_review(
        review_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch(
    "/professional-reviews/{review_id}",
    response_model=ProfessionalReviewDetailResponse,
)
def update_professional_review(
    review_id: str,
    payload: ProfessionalReviewPatchRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).update_review(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/assign",
    response_model=AssignReviewResponse,
)
def assign_professional_review(
    review_id: str,
    payload: AssignReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).assign_lawyer(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/assignment-response",
    response_model=ProfessionalReviewDetailResponse,
)
def respond_professional_review_assignment(
    review_id: str,
    payload: AssignmentResponseRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).assignment_response(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/comments",
    response_model=LawyerCommentDto,
)
def create_professional_review_comment(
    review_id: str,
    payload: CreateCommentRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).create_comment(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch(
    "/professional-reviews/{review_id}/comments/{comment_id}",
    response_model=LawyerCommentDto,
)
def resolve_professional_review_comment(
    review_id: str,
    comment_id: str,
    payload: ResolveCommentRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).resolve_comment(
        review_id,
        comment_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/request-client-action",
    response_model=ProfessionalReviewDetailResponse,
)
def request_professional_review_client_action(
    review_id: str,
    payload: RequestClientActionRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).request_client_action(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/reviewed-files",
    response_model=ReviewedFileDto,
)
def add_professional_review_file(
    review_id: str,
    payload: ReviewedFileCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).add_reviewed_file(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/approve",
    response_model=ProfessionalReviewDetailResponse,
)
def approve_professional_review(
    review_id: str,
    payload: ApproveReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).approve_review(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/reject",
    response_model=ProfessionalReviewDetailResponse,
)
def reject_professional_review(
    review_id: str,
    payload: RejectReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).reject_review(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/cancel",
    response_model=ProfessionalReviewDetailResponse,
)
def cancel_professional_review(
    review_id: str,
    payload: CancelReviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).cancel_review(
        review_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/professional-reviews/{review_id}/ai-summary",
    response_model=AiSummaryResponse,
)
def generate_professional_review_ai_summary(
    review_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ProfessionalReviewService(db).ai_summary(
        review_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


def _json_ready(value):
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
