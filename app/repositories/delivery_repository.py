import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.delivery import (
    CaseClosure,
    DeliveryEvent,
    DeliveryPackage,
    DownloadFile,
    ShareLink,
)


class DeliveryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_package(self, package_id: str | uuid.UUID) -> DeliveryPackage | None:
        parsed = _parse_uuid(package_id)
        if parsed is None:
            return None
        return self.db.get(DeliveryPackage, parsed)

    def latest_package_for_case(self, case_id: uuid.UUID) -> DeliveryPackage | None:
        return (
            self.db.query(DeliveryPackage)
            .filter(
                DeliveryPackage.case_id == case_id,
                DeliveryPackage.is_latest.is_(True),
                DeliveryPackage.deleted_at.is_(None),
            )
            .order_by(desc(DeliveryPackage.version), desc(DeliveryPackage.created_at))
            .first()
        )

    def next_package_version(self, case_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(DeliveryPackage.version))
            .filter(DeliveryPackage.case_id == case_id)
            .scalar()
        )
        return int(current or 0) + 1

    def mark_packages_not_latest(self, case_id: uuid.UUID) -> None:
        (
            self.db.query(DeliveryPackage)
            .filter(
                DeliveryPackage.case_id == case_id,
                DeliveryPackage.is_latest.is_(True),
                DeliveryPackage.deleted_at.is_(None),
            )
            .update({"is_latest": False}, synchronize_session=False)
        )
        self.db.flush()

    def create_package(self, **values: Any) -> DeliveryPackage:
        item = DeliveryPackage(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def get_file(self, file_id: str | uuid.UUID) -> DownloadFile | None:
        parsed = _parse_uuid(file_id)
        if parsed is None:
            return None
        file = self.db.get(DownloadFile, parsed)
        if file is None or file.deleted_at is not None:
            return None
        return file

    def list_files(self, package_id: uuid.UUID) -> list[DownloadFile]:
        return (
            self.db.query(DownloadFile)
            .filter(
                DownloadFile.delivery_package_id == package_id,
                DownloadFile.deleted_at.is_(None),
            )
            .order_by(DownloadFile.created_at.asc(), DownloadFile.file_name.asc())
            .all()
        )

    def list_available_files(self, package_id: uuid.UUID) -> list[DownloadFile]:
        return (
            self.db.query(DownloadFile)
            .filter(
                DownloadFile.delivery_package_id == package_id,
                DownloadFile.deleted_at.is_(None),
                DownloadFile.status == "available",
                DownloadFile.is_unlocked.is_(True),
                DownloadFile.requires_review.is_(False),
            )
            .order_by(DownloadFile.created_at.asc(), DownloadFile.file_name.asc())
            .all()
        )

    def create_file(self, **values: Any) -> DownloadFile:
        item = DownloadFile(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def get_share_link(self, share_link_id: str | uuid.UUID) -> ShareLink | None:
        parsed = _parse_uuid(share_link_id)
        if parsed is None:
            return None
        return self.db.get(ShareLink, parsed)

    def get_share_link_by_hash(self, token_hash: str) -> ShareLink | None:
        return (
            self.db.query(ShareLink)
            .filter(ShareLink.token_hash == token_hash)
            .one_or_none()
        )

    def list_share_links(self, package_id: uuid.UUID) -> list[ShareLink]:
        return (
            self.db.query(ShareLink)
            .filter(ShareLink.delivery_package_id == package_id)
            .order_by(desc(ShareLink.created_at))
            .all()
        )

    def list_active_share_links(self, package_id: uuid.UUID, *, now: datetime) -> list[ShareLink]:
        return (
            self.db.query(ShareLink)
            .filter(
                ShareLink.delivery_package_id == package_id,
                ShareLink.status == "active",
                ShareLink.expires_at > now,
            )
            .order_by(desc(ShareLink.created_at))
            .all()
        )

    def create_share_link(self, **values: Any) -> ShareLink:
        item = ShareLink(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def expire_active_share_links(self, *, now: datetime) -> int:
        count = (
            self.db.query(ShareLink)
            .filter(ShareLink.status == "active", ShareLink.expires_at <= now)
            .update({"status": "expired", "updated_at": now}, synchronize_session=False)
        )
        self.db.flush()
        return int(count or 0)

    def latest_closure_for_case(self, case_id: uuid.UUID) -> CaseClosure | None:
        return (
            self.db.query(CaseClosure)
            .filter(CaseClosure.case_id == case_id)
            .order_by(desc(CaseClosure.created_at))
            .first()
        )

    def create_closure(self, **values: Any) -> CaseClosure:
        item = CaseClosure(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_event(self, **values: Any) -> DeliveryEvent:
        item = DeliveryEvent(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def list_events(
        self,
        *,
        case_id: uuid.UUID,
        limit: int,
        cursor: datetime | None = None,
    ) -> list[DeliveryEvent]:
        query = self.db.query(DeliveryEvent).filter(DeliveryEvent.case_id == case_id)
        if cursor is not None:
            query = query.filter(DeliveryEvent.created_at < cursor)
        return (
            query.order_by(desc(DeliveryEvent.created_at), desc(DeliveryEvent.id))
            .limit(limit)
            .all()
        )


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
