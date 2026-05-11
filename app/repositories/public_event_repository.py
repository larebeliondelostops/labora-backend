from sqlalchemy.orm import Session

from app.models.public_event import PublicEvent
from app.schemas.public import PublicEventCreate


class PublicEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        payload: PublicEventCreate,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> PublicEvent:
        event = PublicEvent(
            event_name=payload.event_name,
            anonymous_id=payload.anonymous_id,
            lead_id=payload.lead_id,
            metadata_json=payload.metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event
