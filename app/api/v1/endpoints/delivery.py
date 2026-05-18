from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import public_rate_limiter
from app.schemas.delivery import (
    AiSummaryRequest,
    AiSummaryResponse,
    ComplementDeliveryRequest,
    ComplementDeliveryResponse,
    CreateShareLinkRequest,
    CreateShareLinkResponse,
    DeliveryCenterResponse,
    DeliveryEventsResponse,
    FileDownloadResponse,
    RevokeShareLinkResponse,
    SharedDeliveryResponse,
)
from app.services.delivery_service import (
    DeliveryAiSummaryService,
    DeliveryPackageService,
    DownloadFileService,
    ShareLinkService,
)


router = APIRouter()


@router.get(
    "/cases/{case_id}/delivery",
    response_model=DeliveryCenterResponse,
)
def get_delivery_center(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DeliveryPackageService(db).get_delivery_center(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/files/{file_id}/download",
    response_model=FileDownloadResponse,
)
def download_file(
    file_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    _check_download_rate_limit(request, user_id=str(user.id))
    return DownloadFileService(db).download_url(
        file_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/files/{file_id}/file")
def stream_file(
    file_id: str,
    expires: Annotated[int, Query()],
    token: Annotated[str, Query()],
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return DownloadFileService(db).stream_file(file_id, expires=expires, token=token)


@router.post(
    "/cases/{case_id}/share-links",
    response_model=CreateShareLinkResponse,
)
def create_share_link(
    case_id: str,
    payload: CreateShareLinkRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ShareLinkService(db).create(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/share/delivery/{token}",
    response_model=SharedDeliveryResponse,
)
def get_shared_delivery(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _check_public_share_rate_limit(request)
    return ShareLinkService(db).get_shared_delivery(
        token,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/share/delivery/{token}/files/{file_id}/download",
    response_model=FileDownloadResponse,
)
def download_shared_file(
    token: str,
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _check_public_share_rate_limit(request)
    return ShareLinkService(db).download_from_share(
        token,
        file_id,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.delete(
    "/cases/{case_id}/share-links/{share_link_id}",
    response_model=RevokeShareLinkResponse,
)
def revoke_share_link(
    case_id: str,
    share_link_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return ShareLinkService(db).revoke(
        case_id,
        share_link_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/delivery/complement",
    response_model=ComplementDeliveryResponse,
)
def complement_delivery(
    case_id: str,
    payload: ComplementDeliveryRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DeliveryPackageService(db).complement(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get(
    "/cases/{case_id}/delivery/events",
    response_model=DeliveryEventsResponse,
)
def list_delivery_events(
    case_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DeliveryPackageService(db).list_events(
        case_id,
        limit=limit,
        cursor=cursor,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/delivery/ai-summary",
    response_model=AiSummaryResponse,
)
def regenerate_ai_summary(
    case_id: str,
    payload: AiSummaryRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return DeliveryAiSummaryService(db).regenerate(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


def _check_public_share_rate_limit(request: Request) -> None:
    public_rate_limiter.check(
        key=f"delivery-share:{get_client_ip(request)}",
        limit=settings.delivery_public_share_rate_limit,
        window_seconds=60,
    )


def _check_download_rate_limit(request: Request, *, user_id: str) -> None:
    public_rate_limiter.check(
        key=f"delivery-download:{user_id}:{get_client_ip(request)}",
        limit=settings.delivery_download_rate_limit,
        window_seconds=60,
    )
