from sqlalchemy.orm import sessionmaker

from app.models.user import User
from app.services.delivery_service import (
    DeliveryAiSummaryService,
    DeliveryPackageService,
    ShareLinkService,
)


def run_delivery_package_builder_background(
    case_id: str,
    *,
    actor_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    session_factory: sessionmaker,
) -> None:
    db = session_factory()
    try:
        actor = db.get(User, actor_id) if actor_id else None
        DeliveryPackageService(db).build_from_generated_files(
            case_id,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    finally:
        db.close()


def run_share_link_expiration_background(*, session_factory: sessionmaker) -> None:
    db = session_factory()
    try:
        ShareLinkService(db).expire_links()
    finally:
        db.close()


def run_delivery_ai_summary_background(
    case_id: str,
    *,
    actor_id: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    force: bool = False,
    session_factory: sessionmaker,
) -> None:
    db = session_factory()
    try:
        actor = db.get(User, actor_id) if actor_id else None
        if actor is None:
            return
        from app.schemas.delivery import AiSummaryRequest

        DeliveryAiSummaryService(db).regenerate(
            case_id,
            AiSummaryRequest(force=force),
            user=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    finally:
        db.close()
