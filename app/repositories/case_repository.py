import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session

from app.models.case import (
    CaseHistoryEvent,
    CaseOwner,
    CaseStatusHistory,
    CaseTag,
    LaboraCase,
)


class CaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, case_id: str | uuid.UUID) -> LaboraCase | None:
        parsed_case_id = _parse_uuid(case_id)
        if parsed_case_id is None:
            return None
        return self.db.get(LaboraCase, parsed_case_id)

    def case_number_exists(self, case_number: str) -> bool:
        return (
            self.db.query(LaboraCase.id)
            .filter(LaboraCase.case_number == case_number)
            .first()
            is not None
        )

    def next_case_number(self, year: int) -> str:
        prefix = f"CASO-{year}-"
        last = (
            self.db.query(LaboraCase.case_number)
            .filter(LaboraCase.case_number.like(f"{prefix}%"))
            .order_by(desc(LaboraCase.case_number))
            .first()
        )
        next_value = 1
        if last:
            try:
                next_value = int(last[0].rsplit("-", 1)[1]) + 1
            except (IndexError, ValueError):
                next_value = self._count_case_numbers_for_year(prefix) + 1
        return f"{prefix}{next_value:06d}"

    def create(self, **values: Any) -> LaboraCase:
        case = LaboraCase(**values)
        self.db.add(case)
        self.db.flush()
        return case

    def list_user_cases(
        self,
        *,
        user_id: uuid.UUID,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[LaboraCase], int]:
        query = (
            self.db.query(LaboraCase)
            .outerjoin(CaseOwner, CaseOwner.case_id == LaboraCase.id)
            .filter(
                LaboraCase.deleted_at.is_(None),
                or_(
                    LaboraCase.owner_user_id == user_id,
                    CaseOwner.user_id == user_id,
                ),
            )
        )
        if status:
            query = query.filter(LaboraCase.status == status)
        total = (
            query.with_entities(func.count(func.distinct(LaboraCase.id))).scalar()
            or 0
        )
        cases = (
            query.distinct()
            .order_by(desc(LaboraCase.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return cases, total

    def list_admin_cases(
        self,
        *,
        q: str | None,
        status: str | None,
        case_type_requested: str | None,
        situation_type: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        updated_from: datetime | None,
        updated_to: datetime | None,
        assigned_to: uuid.UUID | None,
        tag: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[LaboraCase], int]:
        query = self.db.query(LaboraCase).filter(LaboraCase.deleted_at.is_(None))
        if assigned_to is not None:
            query = query.join(CaseOwner, CaseOwner.case_id == LaboraCase.id).filter(
                CaseOwner.user_id == assigned_to,
                CaseOwner.role.in_(["legal_reviewer", "admin_assignee"]),
            )
        if tag:
            query = query.join(CaseTag, CaseTag.case_id == LaboraCase.id).filter(
                CaseTag.tag == tag,
            )
        if q:
            like_q = f"%{q.strip()}%"
            query = query.filter(
                or_(
                    LaboraCase.case_number.ilike(like_q),
                    LaboraCase.holder_first_name.ilike(like_q),
                    LaboraCase.holder_last_name.ilike(like_q),
                    LaboraCase.holder_document_number.ilike(like_q),
                ),
            )
        if status:
            query = query.filter(LaboraCase.status == status)
        if case_type_requested:
            query = query.filter(LaboraCase.case_type_requested == case_type_requested)
        if situation_type:
            query = query.filter(LaboraCase.situation_type == situation_type)
        if created_from:
            query = query.filter(LaboraCase.created_at >= created_from)
        if created_to:
            query = query.filter(LaboraCase.created_at <= created_to)
        if updated_from:
            query = query.filter(LaboraCase.updated_at >= updated_from)
        if updated_to:
            query = query.filter(LaboraCase.updated_at <= updated_to)

        total = (
            query.with_entities(func.count(func.distinct(LaboraCase.id))).scalar()
            or 0
        )
        cases = (
            query.distinct()
            .order_by(desc(LaboraCase.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return cases, total

    def create_owner(
        self,
        *,
        case_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        permissions: dict[str, Any],
    ) -> CaseOwner:
        existing = (
            self.db.query(CaseOwner)
            .filter(
                CaseOwner.case_id == case_id,
                CaseOwner.user_id == user_id,
                CaseOwner.role == role,
            )
            .one_or_none()
        )
        if existing is not None:
            existing.permissions = permissions
            return existing
        owner = CaseOwner(
            case_id=case_id,
            user_id=user_id,
            role=role,
            permissions=permissions,
        )
        self.db.add(owner)
        self.db.flush()
        return owner

    def get_owner(
        self,
        *,
        case_id: uuid.UUID,
        user_id: uuid.UUID,
        roles: set[str] | None = None,
    ) -> CaseOwner | None:
        query = self.db.query(CaseOwner).filter(
            CaseOwner.case_id == case_id,
            CaseOwner.user_id == user_id,
        )
        if roles:
            query = query.filter(CaseOwner.role.in_(roles))
        return query.first()

    def create_status_history(
        self,
        *,
        case_id: uuid.UUID,
        previous_status: str | None,
        new_status: str,
        reason: str | None,
        changed_by_user_id: uuid.UUID | None,
        changed_by_role: str,
        source_module: str,
        metadata: dict[str, Any] | None,
    ) -> CaseStatusHistory:
        item = CaseStatusHistory(
            case_id=case_id,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            changed_by_user_id=changed_by_user_id,
            changed_by_role=changed_by_role,
            source_module=source_module,
            metadata_json=metadata,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def create_history_event(
        self,
        *,
        case_id: uuid.UUID,
        event_type: str,
        title: str,
        description: str | None,
        visibility: str,
        severity: str,
        created_by_user_id: uuid.UUID | None,
        metadata: dict[str, Any] | None,
    ) -> CaseHistoryEvent:
        event = CaseHistoryEvent(
            case_id=case_id,
            event_type=event_type,
            title=title,
            description=description,
            visibility=visibility,
            severity=severity,
            created_by_user_id=created_by_user_id,
            metadata_json=metadata,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def list_history_events(
        self,
        *,
        case_id: uuid.UUID,
        include_internal: bool,
        sort: str,
    ) -> list[CaseHistoryEvent]:
        query = self.db.query(CaseHistoryEvent).filter(
            CaseHistoryEvent.case_id == case_id,
        )
        if not include_internal:
            query = query.filter(CaseHistoryEvent.visibility.in_(["user", "both"]))
        order_by = asc(CaseHistoryEvent.created_at) if sort == "asc" else desc(CaseHistoryEvent.created_at)
        return query.order_by(order_by).all()

    def add_tag(
        self,
        *,
        case_id: uuid.UUID,
        tag: str,
        source: str,
        confidence: float | None,
    ) -> CaseTag:
        existing = (
            self.db.query(CaseTag)
            .filter(CaseTag.case_id == case_id, CaseTag.tag == tag)
            .one_or_none()
        )
        if existing is not None:
            existing.source = source
            existing.confidence = confidence
            return existing
        item = CaseTag(
            case_id=case_id,
            tag=tag,
            source=source,
            confidence=confidence,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def list_tags(self, case_id: uuid.UUID) -> list[CaseTag]:
        return (
            self.db.query(CaseTag)
            .filter(CaseTag.case_id == case_id)
            .order_by(asc(CaseTag.tag))
            .all()
        )

    def _count_case_numbers_for_year(self, prefix: str) -> int:
        return (
            self.db.query(func.count(LaboraCase.id))
            .filter(LaboraCase.case_number.like(f"{prefix}%"))
            .scalar()
            or 0
        )


def _parse_uuid(value: str | uuid.UUID) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
