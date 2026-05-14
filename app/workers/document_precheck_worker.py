from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.document_precheck_repository import DocumentPrecheckRepository
from app.repositories.document_repository import DocumentRepository
from app.services.document_precheck_service import DocumentPrecheckService


def run_document_precheck_job(
    db: Session,
    *,
    precheck_id: str,
    actor: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict:
    precheck = DocumentPrecheckRepository(db).get(precheck_id)
    if precheck is None:
        return {"type": "document-precheck.process", "status": "skipped"}
    document = DocumentRepository(db).get(precheck.document_id)
    if document is None:
        return {"type": "document-precheck.process", "status": "skipped"}
    DocumentPrecheckService(db)._process_precheck(
        precheck,
        document=document,
        actor=actor,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()
    return {
        "type": "document-precheck.process",
        "status": precheck.status,
        "precheckId": str(precheck.id),
    }
