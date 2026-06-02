from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_current_user_context,
    get_user_agent,
)
from app.core.database import get_db
from app.schemas.payment import OrderCreateRequest
from app.schemas.pension import (
    CaseEntitlementResponse,
    LegalDraftPaymentOrderResponse,
    LegalDraftPaywallResponse,
    LegalRouteResponse,
    PensionAffiliateProfilePatch,
    PensionAffiliateProfileResponse,
    PensionAlertResponse,
    PensionAssumptionsRequest,
    PensionAssumptionsResponse,
    PensionContributionResponse,
    PensionSimulationResponse,
)
from app.services.pension_simulation_service import PensionSimulationService


router = APIRouter()


@router.get("/cases/{case_id}/pension/profile", response_model=PensionAffiliateProfileResponse)
def get_pension_profile(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).get_profile(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.patch("/cases/{case_id}/pension/profile", response_model=PensionAffiliateProfileResponse)
def update_pension_profile(
    case_id: str,
    payload: PensionAffiliateProfilePatch,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PensionSimulationService(db).update_profile(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/pension/contributions")
def list_pension_contributions(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict[str, list[PensionContributionResponse]]:
    user, _payload = context
    return PensionSimulationService(db).list_contributions(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/pension/assumptions/defaults")
def default_pension_assumptions(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).default_assumptions(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/pension/assumptions",
    response_model=PensionAssumptionsResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pension_assumptions(
    case_id: str,
    payload: PensionAssumptionsRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PensionSimulationService(db).create_assumptions(
        case_id,
        payload,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post("/cases/{case_id}/pension/simulations/run", response_model=PensionSimulationResponse)
def run_pension_simulation(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).run_simulation(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/pension/simulations/latest", response_model=PensionSimulationResponse)
def latest_pension_simulation(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).latest_simulation(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/pension/simulations/{simulation_id}", response_model=PensionSimulationResponse)
def get_pension_simulation(
    case_id: str,
    simulation_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).get_simulation(
        case_id,
        simulation_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/pension/alerts")
def list_pension_alerts(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict[str, list[PensionAlertResponse]]:
    user, _payload = context
    return PensionSimulationService(db).list_alerts(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/legal-route", response_model=LegalRouteResponse)
def get_legal_route(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).legal_route(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/entitlements")
def get_case_entitlements(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict[str, list[CaseEntitlementResponse]]:
    user, _payload = context
    return PensionSimulationService(db).entitlements(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.get("/cases/{case_id}/paywall/legal-draft", response_model=LegalDraftPaywallResponse)
def get_legal_draft_paywall(
    case_id: str,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _payload = context
    return PensionSimulationService(db).legal_draft_paywall(
        case_id,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


@router.post(
    "/cases/{case_id}/payments/legal-draft",
    response_model=LegalDraftPaymentOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_legal_draft_payment_order(
    case_id: str,
    payload: OrderCreateRequest,
    request: Request,
    context=Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> dict:
    user, _token_payload = context
    return PensionSimulationService(db).create_legal_draft_order(
        case_id,
        return_url=payload.return_url,
        cancel_url=payload.cancel_url,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )

