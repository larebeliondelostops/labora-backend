from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import public_rate_limiter
from app.schemas.public import (
    FaqListResponse,
    IntentClassificationCreate,
    IntentClassificationResponse,
    LeadCreate,
    LeadCreateResponse,
    PublicEventCreate,
    PublicEventResponse,
    PublicHomeResponse,
)
from app.services.lead_service import LeadService
from app.services.public_content_service import PublicContentService
from app.services.public_event_service import PublicEventService
from app.services.visitor_intent_service import VisitorIntentService

router = APIRouter()
leads_router = APIRouter()


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.get("/home", response_model=PublicHomeResponse)
def get_home(db: Session = Depends(get_db)) -> PublicHomeResponse:
    return PublicContentService(db).get_home()


@router.get("/faqs", response_model=FaqListResponse)
def get_faqs(
    category: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> FaqListResponse:
    return PublicContentService(db).list_faqs(category=category, limit=limit)


@leads_router.post("", response_model=LeadCreateResponse, status_code=201)
def create_lead(
    payload: LeadCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> LeadCreateResponse:
    ip_address = _client_ip(request)
    public_rate_limiter.check(
        key=f"lead:ip:{ip_address}",
        limit=10,
        window_seconds=3600,
    )
    public_rate_limiter.check(
        key=f"lead:email:{str(payload.email).lower()}",
        limit=3,
        window_seconds=3600,
    )

    return LeadService(db).create(
        payload,
        ip_address=ip_address,
        user_agent=_user_agent(request),
    )


@router.post("/events", response_model=PublicEventResponse, status_code=201)
def create_public_event(
    payload: PublicEventCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> PublicEventResponse:
    ip_address = _client_ip(request)
    public_rate_limiter.check(
        key=f"event:ip:{ip_address}",
        limit=60,
        window_seconds=60,
    )
    return PublicEventService(db).create(
        payload,
        ip_address=ip_address,
        user_agent=_user_agent(request),
    )


@router.post("/intent-classification", response_model=IntentClassificationResponse)
def classify_intent(
    payload: IntentClassificationCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> IntentClassificationResponse:
    ip_address = _client_ip(request)
    public_rate_limiter.check(
        key=f"intent:ip:{ip_address}",
        limit=20,
        window_seconds=3600,
    )
    return VisitorIntentService(db).classify(payload)
