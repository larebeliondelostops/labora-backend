import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user import User
from app.services.report_service import ReportExportService, ReportService


def run_report_generation_job(
    db: Session,
    *,
    report_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    include_sections: list[str] | None = None,
) -> dict:
    return ReportService(db).run_generation(
        report_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
        include_sections=include_sections or [],
    )


def run_report_generation_background(
    report_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    include_sections: list[str] | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_report_generation_job(
            db,
            report_id=report_id,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
            include_sections=include_sections,
        )
    finally:
        db.close()


def run_report_export_job(
    db: Session,
    *,
    export_file_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return ReportExportService(db).run_export(
        export_file_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_report_export_background(
    export_file_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_report_export_job(
            db,
            export_file_id=export_file_id,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    finally:
        db.close()


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
