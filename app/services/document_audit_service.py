import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository


class DocumentAuditService:
    def __init__(self, db: Session) -> None:
        self.audit_events = AuditEventRepository(db)

    def record(
        self,
        event_name: str,
        *,
        actor: User | None,
        document: Document | None = None,
        case_id: uuid.UUID | None = None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        event_metadata = {
            "actorType": _actor_type(actor),
            "caseId": str(case_id or document.case_id) if (case_id or document) else None,
            "documentId": str(document.id) if document else None,
            "sourceModule": "documents",
        }
        if metadata:
            event_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_name,
            entity_type="document",
            entity_id=document.id if document else None,
            actor_user_id=actor.id if actor else None,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata=_json_safe(event_metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )


def _actor_type(actor: User | None) -> str:
    if actor is None:
        return "system"
    if actor.role in {"admin", "legal_admin"}:
        return "admin"
    if actor.role == "legal_reviewer":
        return "legal_reviewer"
    if actor.role == "system":
        return "system"
    return "user"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
