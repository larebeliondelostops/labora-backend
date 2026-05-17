import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user import User
from app.services.full_analysis_service import FullAnalysisService
from app.services.pre_analysis_service import PreAnalysisService


def run_analysis_job() -> None:
    """Analysis worker placeholder."""


def run_pre_analysis_job(
    db: Session,
    *,
    pre_analysis_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return PreAnalysisService(db).run(
        pre_analysis_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_full_analysis_job(
    db: Session,
    *,
    full_analysis_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return FullAnalysisService(db).run(
        full_analysis_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_full_analysis_background(
    full_analysis_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_full_analysis_job(
            db,
            full_analysis_id=full_analysis_id,
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
