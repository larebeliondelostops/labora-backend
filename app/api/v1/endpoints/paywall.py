from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.paywall import (
    AdminPaywallPreviewListResponse,
    AdminRejectPreviewRequest,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    ConversionEventCreateRequest,
    ConversionEventResponse,
    PaywallConfigResponse,
    PreviewCreateRequest,
    PreviewResponse,
    PreviewStartResponse,
)
from app.services.paywall_service import PaywallPreviewService


router = APIRouter()
admin_router = APIRouter()


@router.get(
    "/cases/{case_id}/preview",
    response_model=PreviewResponse,
)
def get_case_preview(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaywallPreviewService(db).get_preview(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/preview",
    response_model=PreviewStartResponse,
)
def create_or_refresh_case_preview(
    case_id: str,
    request: Request,
    payload: PreviewCreateRequest | None = Body(default=None),
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    payload = payload or PreviewCreateRequest()
    return PaywallPreviewService(db).create_or_refresh(
        case_id,
        force_refresh=payload.force_refresh,
        reason=payload.reason,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/paywall",
    response_model=PaywallConfigResponse,
)
def get_case_paywall(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaywallPreviewService(db).get_paywall_config(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/checkout/session",
    response_model=CheckoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_checkout_session(
    case_id: str,
    payload: CheckoutSessionRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PaywallPreviewService(db).create_checkout_session(
        case_id,
        source=payload.source,
        return_url=payload.return_url,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/analytics/conversion-events",
    response_model=ConversionEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversion_event(
    payload: ConversionEventCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PaywallPreviewService(db).record_conversion_event(
        case_id=payload.case_id,
        event_name=payload.event_name,
        source=payload.source,
        metadata=payload.metadata,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/payments/epayco/confirmation")
async def confirm_epayco_payment(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    payload = await _epayco_payload(request)
    return PaywallPreviewService(db).confirm_epayco_payment(
        payload=payload,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.get(
    "/paywall-previews",
    response_model=AdminPaywallPreviewListResponse,
)
def list_admin_paywall_previews(
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaywallPreviewService(db).list_admin_previews(
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        user=user,
    )


@admin_router.post(
    "/paywall-previews/{preview_id}/approve",
    response_model=PreviewResponse,
)
def approve_paywall_preview(
    preview_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PaywallPreviewService(db).approve_preview(
        preview_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@admin_router.post(
    "/paywall-previews/{preview_id}/reject",
    response_model=PreviewResponse,
)
def reject_paywall_preview(
    preview_id: str,
    payload: AdminRejectPreviewRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PaywallPreviewService(db).reject_preview(
        preview_id,
        reason=payload.reason,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


async def _epayco_payload(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    raw_body = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}
