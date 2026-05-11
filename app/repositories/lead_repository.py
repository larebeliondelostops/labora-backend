from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.lead import Lead
from app.schemas.public import LeadCreate


class LeadRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def count_recent_by_email(self, email: str) -> int:
        return self.db.query(Lead).filter(Lead.email == email.lower()).count()

    def create(
        self,
        payload: LeadCreate,
        *,
        ip_address: str | None,
        user_agent: str | None,
        intent: str | None = None,
        intent_confidence: Decimal | None = None,
    ) -> Lead:
        utm = payload.utm
        lead = Lead(
            full_name=payload.full_name,
            email=str(payload.email).lower(),
            phone=payload.phone,
            service_interest=payload.service_interest,
            message=payload.message,
            source=payload.source,
            utm_source=utm.source if utm else None,
            utm_medium=utm.medium if utm else None,
            utm_campaign=utm.campaign if utm else None,
            utm_content=utm.content if utm else None,
            utm_term=utm.term if utm else None,
            status="new",
            intent=intent,
            intent_confidence=intent_confidence,
            accepted_privacy_notice=payload.accepted_privacy_notice,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(lead)
        self.db.commit()
        self.db.refresh(lead)
        return lead
