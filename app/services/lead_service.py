from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.public_events import LANDING_PUBLICA_SUBMITTED
from app.repositories.lead_repository import LeadRepository
from app.repositories.public_event_repository import PublicEventRepository
from app.schemas.public import LeadCreate, LeadCreateResponse, NextAction, PublicEventCreate


class LeadService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.leads = LeadRepository(db)
        self.events = PublicEventRepository(db)

    def create(
        self,
        payload: LeadCreate,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> LeadCreateResponse:
        lead = self.leads.create(
            payload,
            ip_address=ip_address,
            user_agent=user_agent,
            intent=self._classify_lightweight(payload),
            intent_confidence=Decimal("0.60") if payload.message else None,
        )

        self.events.create(
            PublicEventCreate(
                event_name=LANDING_PUBLICA_SUBMITTED,
                anonymous_id=None,
                lead_id=str(lead.id),
                metadata={
                    "source": payload.source,
                    "serviceInterest": payload.service_interest,
                },
            ),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return LeadCreateResponse(
            id=str(lead.id),
            status=lead.status,
            message="Recibimos tus datos. Puedes iniciar tu analisis creando una cuenta.",
            next_action=NextAction(label="Crear cuenta", url="/registro"),
        )

    def _classify_lightweight(self, payload: LeadCreate) -> str | None:
        if not payload.message:
            return payload.service_interest

        text = payload.message.lower()
        if "semana" in text or "historia laboral" in text:
            return "possible_missing_weeks"
        if "pension" in text or "reliquidacion" in text:
            return "pension_review"
        return payload.service_interest
