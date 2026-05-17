import uuid
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.case_result import (
    CaseResult,
    EconomicEstimate,
    FinalViability,
    RecommendedRoute,
    ResultAuditEvent,
    ResultCard,
    ResultInconsistency,
)


VISIBLE_RESULT_STATUSES = {"completed", "approved"}


class CaseResultRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, result_id: str | uuid.UUID) -> CaseResult | None:
        parsed = _parse_uuid(result_id)
        if parsed is None:
            return None
        return self.db.get(CaseResult, parsed)

    def latest_for_case(self, case_id: uuid.UUID) -> CaseResult | None:
        return (
            self.db.query(CaseResult)
            .filter(CaseResult.case_id == case_id, CaseResult.deleted_at.is_(None))
            .order_by(desc(CaseResult.version), desc(CaseResult.created_at))
            .first()
        )

    def latest_visible_for_case(self, case_id: uuid.UUID) -> CaseResult | None:
        return (
            self.db.query(CaseResult)
            .filter(
                CaseResult.case_id == case_id,
                CaseResult.deleted_at.is_(None),
                CaseResult.status.in_(VISIBLE_RESULT_STATUSES),
                CaseResult.is_visible_to_user.is_(True),
            )
            .order_by(desc(CaseResult.version), desc(CaseResult.created_at))
            .first()
        )

    def latest_approved_for_case(self, case_id: uuid.UUID) -> CaseResult | None:
        return (
            self.db.query(CaseResult)
            .filter(
                CaseResult.case_id == case_id,
                CaseResult.deleted_at.is_(None),
                CaseResult.status == "approved",
            )
            .order_by(desc(CaseResult.version), desc(CaseResult.created_at))
            .first()
        )

    def next_version(self, case_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(CaseResult.version))
            .filter(CaseResult.case_id == case_id)
            .scalar()
        )
        return int(current or 0) + 1

    def create(self, **values: Any) -> CaseResult:
        result = CaseResult(**values)
        self.db.add(result)
        self.db.flush()
        return result

    def replace_children(
        self,
        *,
        result: CaseResult,
        final_viability: dict[str, Any],
        economic_estimate: dict[str, Any],
        recommended_route: dict[str, Any],
        cards: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
    ) -> None:
        self.db.query(ResultInconsistency).filter(ResultInconsistency.case_result_id == result.id).delete()
        self.db.query(ResultCard).filter(ResultCard.case_result_id == result.id).delete()
        self.db.query(RecommendedRoute).filter(RecommendedRoute.case_result_id == result.id).delete()
        self.db.query(EconomicEstimate).filter(EconomicEstimate.case_result_id == result.id).delete()
        self.db.query(FinalViability).filter(FinalViability.case_result_id == result.id).delete()
        self.db.flush()

        viability = FinalViability(case_result_id=result.id, **final_viability)
        estimate = EconomicEstimate(case_result_id=result.id, **economic_estimate)
        route = RecommendedRoute(case_result_id=result.id, **recommended_route)
        self.db.add_all([viability, estimate, route])
        self.db.flush()

        result.final_viability_id = viability.id
        result.economic_estimate_id = estimate.id
        result.recommended_route_id = route.id

        for item in cards:
            self.db.add(ResultCard(case_result_id=result.id, **item))
        for item in inconsistencies:
            self.db.add(ResultInconsistency(case_result_id=result.id, **item))
        self.db.flush()

    def create_audit_event(
        self,
        *,
        case_id: uuid.UUID,
        case_result_id: uuid.UUID | None,
        actor_id: uuid.UUID | None,
        actor_role: str,
        event_type: str,
        previous_status: str | None = None,
        new_status: str | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ResultAuditEvent:
        event = ResultAuditEvent(
            case_id=case_id,
            case_result_id=case_result_id,
            actor_id=actor_id,
            actor_role=actor_role,
            event_type=event_type,
            previous_status=previous_status,
            new_status=new_status,
            metadata_json=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def list_cards(self, result_id: uuid.UUID) -> list[ResultCard]:
        return (
            self.db.query(ResultCard)
            .filter(ResultCard.case_result_id == result_id)
            .order_by(asc(ResultCard.sort_order), asc(ResultCard.created_at))
            .all()
        )

    def list_inconsistencies(self, result_id: uuid.UUID) -> list[ResultInconsistency]:
        return (
            self.db.query(ResultInconsistency)
            .filter(ResultInconsistency.case_result_id == result_id)
            .order_by(asc(ResultInconsistency.sort_order), asc(ResultInconsistency.created_at))
            .all()
        )


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
