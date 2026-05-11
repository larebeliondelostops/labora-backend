from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.public_events import ALLOWED_PUBLIC_EVENT_NAMES
from app.repositories.public_event_repository import PublicEventRepository
from app.schemas.public import PublicEventCreate, PublicEventResponse


class PublicEventService:
    def __init__(self, db: Session) -> None:
        self.events = PublicEventRepository(db)

    def create(
        self,
        payload: PublicEventCreate,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> PublicEventResponse:
        if payload.event_name not in ALLOWED_PUBLIC_EVENT_NAMES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unsupported public event name",
            )

        event = self.events.create(
            payload,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return PublicEventResponse(id=str(event.id))
