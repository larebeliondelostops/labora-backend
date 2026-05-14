from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.user import User
from app.repositories.document_repository import DocumentRepository
from app.services.document_ai_classifier_service import DocumentAiClassifierService
from app.services.document_audit_service import DocumentAuditService
from app.services.document_validation_service import DocumentValidationService
from app.utils.dates import utc_now


class DocumentJobQueue:
    """In-process queue facade; swap with Celery/RQ without changing callers."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.validator = DocumentValidationService(db)
        self.documents = DocumentRepository(db)
        self.classifier = DocumentAiClassifierService()
        self.audit = DocumentAuditService(db)

    def enqueue_post_upload_jobs(
        self,
        document: Document,
        *,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> list[dict]:
        validation_result = self.validator.validate_document(
            document,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        jobs = [{"type": "document_validation", "status": "completed"}]
        if document.status not in {"rejected", "failed"}:
            self._run_classification(
                document,
                text_sample=validation_result["textSample"],
                actor=actor,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            jobs.append({"type": "document_classification", "status": "completed"})
        return jobs

    def _run_classification(
        self,
        document: Document,
        *,
        text_sample: str,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if document.classification_source == "manual":
            return
        previous_state = {
            "status": document.status,
            "documentTypeCode": document.document_type.code
            if document.document_type
            else None,
            "classificationSource": document.classification_source,
        }
        result = self.classifier.classify(
            filename=document.original_filename,
            extracted_text=text_sample,
            mime_type=document.mime_type,
        )
        document_type = self.documents.get_type_by_code(result.document_type_code)
        if document_type is not None and result.confidence >= 0.8:
            document.document_type_id = document_type.id
        document.ai_confidence = result.confidence
        document.classification_source = "ai" if result.confidence >= 0.5 else "unknown"
        document.updated_at = utc_now()
        if result.confidence < 0.8:
            document.status = "requires_review"
            document.validation_status = "requires_review"
        self.audit.record(
            "carga_documental.classified",
            actor=actor,
            document=document,
            previous_state=previous_state,
            new_state={
                "status": document.status,
                "classificationSource": document.classification_source,
                "aiConfidence": result.confidence,
                "suggestedDocumentTypeCode": result.document_type_code,
            },
            metadata={
                "reason": result.reason,
                "signals": result.signals,
                "requiresHumanReview": result.requires_human_review,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
