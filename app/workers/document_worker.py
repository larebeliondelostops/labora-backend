from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.document_repository import DocumentRepository
from app.services.document_job_queue import DocumentJobQueue


def run_document_job(
    db: Session,
    *,
    document_id: str,
    actor: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> list[dict]:
    document = DocumentRepository(db).get(document_id)
    if document is None:
        return [{"type": "document_validation", "status": "skipped"}]
    return DocumentJobQueue(db).enqueue_post_upload_jobs(
        document,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )
