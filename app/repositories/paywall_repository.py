import uuid
from typing import Any

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.paywall import ConversionEvent, LockedFeature, Paywall, PreviewResult


class PaywallRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_preview(self, preview_id: str | uuid.UUID) -> PreviewResult | None:
        parsed = _parse_uuid(preview_id)
        if parsed is None:
            return None
        return self.db.get(PreviewResult, parsed)

    def latest_preview_for_case(self, case_id: uuid.UUID) -> PreviewResult | None:
        return (
            self.db.query(PreviewResult)
            .filter(PreviewResult.case_id == case_id)
            .order_by(desc(PreviewResult.created_at))
            .first()
        )

    def latest_preview_for_preanalysis(
        self,
        *,
        case_id: uuid.UUID,
        source_preanalysis_id: uuid.UUID,
    ) -> PreviewResult | None:
        return (
            self.db.query(PreviewResult)
            .filter(
                PreviewResult.case_id == case_id,
                PreviewResult.source_preanalysis_id == source_preanalysis_id,
            )
            .order_by(desc(PreviewResult.created_at))
            .first()
        )

    def create_preview(self, **values: Any) -> PreviewResult:
        item = PreviewResult(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_paywall_for_case(self, case_id: uuid.UUID) -> Paywall | None:
        return (
            self.db.query(Paywall)
            .filter(Paywall.case_id == case_id)
            .order_by(desc(Paywall.created_at))
            .first()
        )

    def paywall_for_preview(self, preview_result_id: uuid.UUID) -> Paywall | None:
        return (
            self.db.query(Paywall)
            .filter(Paywall.preview_result_id == preview_result_id)
            .order_by(desc(Paywall.created_at))
            .first()
        )

    def create_paywall(self, **values: Any) -> Paywall:
        item = Paywall(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def replace_locked_features(
        self,
        *,
        paywall_id: uuid.UUID,
        features: list[dict[str, Any]],
    ) -> None:
        self.db.query(LockedFeature).filter(LockedFeature.paywall_id == paywall_id).delete()
        self.db.flush()
        for index, feature in enumerate(features):
            self.db.add(
                LockedFeature(
                    paywall_id=paywall_id,
                    feature_key=feature["key"],
                    title=feature["title"],
                    description=feature.get("description"),
                    is_highlighted=feature.get("isHighlighted", False),
                    sort_order=index,
                )
            )
        self.db.flush()

    def list_locked_features(self, paywall_id: uuid.UUID) -> list[LockedFeature]:
        return (
            self.db.query(LockedFeature)
            .filter(LockedFeature.paywall_id == paywall_id)
            .order_by(LockedFeature.sort_order.asc())
            .all()
        )

    def create_conversion_event(
        self,
        *,
        event_name: str,
        source: str,
        case_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        metadata: dict[str, Any] | None,
    ) -> ConversionEvent:
        item = ConversionEvent(
            event_name=event_name,
            source=source,
            case_id=case_id,
            user_id=user_id,
            metadata_json=metadata,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def list_admin_previews(
        self,
        *,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PreviewResult], int]:
        query = self.db.query(PreviewResult)
        if status_filter:
            query = query.filter(PreviewResult.status == status_filter)
        total = query.with_entities(func.count(PreviewResult.id)).scalar() or 0
        items = (
            query.order_by(desc(PreviewResult.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
