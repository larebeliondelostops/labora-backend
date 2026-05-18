import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user import User
from app.services.legal_action_service import LegalActionService


def run_legal_draft_generation_job(
    db: Session,
    *,
    job_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return LegalActionService(db).run_generation_job(
        job_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_legal_draft_generation_background(
    job_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_legal_draft_generation_job(
            db,
            job_id=job_id,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    finally:
        db.close()


def run_draft_section_regeneration_job(
    db: Session,
    *,
    job_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return LegalActionService(db).run_section_regeneration_job(
        job_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_draft_section_regeneration_background(
    job_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_draft_section_regeneration_job(
            db,
            job_id=job_id,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    finally:
        db.close()


def run_draft_quality_check_job(
    db: Session,
    *,
    job_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return LegalActionService(db).run_quality_check_job(
        job_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_draft_quality_check_background(
    job_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_draft_quality_check_job(
            db,
            job_id=job_id,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    finally:
        db.close()


def run_draft_export_job(
    db: Session,
    *,
    job_id: str,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    return LegalActionService(db).run_export_job(
        job_id,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def run_draft_export_background(
    job_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict:
    db = session_factory()
    try:
        actor = db.get(User, _parse_uuid(actor_id)) if actor_id else None
        return run_draft_export_job(
            db,
            job_id=job_id,
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
