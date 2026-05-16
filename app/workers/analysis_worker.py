from sqlalchemy.orm import Session

from app.models.user import User
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
