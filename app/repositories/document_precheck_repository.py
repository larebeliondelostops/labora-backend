import uuid
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.document_precheck import (
    AiConfidence,
    DocumentIssue,
    DocumentPrecheck,
    OcrJob,
    OcrPageResult,
)


class DocumentPrecheckRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, precheck_id: str | uuid.UUID) -> DocumentPrecheck | None:
        parsed_id = _parse_uuid(precheck_id)
        if parsed_id is None:
            return None
        return self.db.get(DocumentPrecheck, parsed_id)

    def latest_for_document(self, document_id: str | uuid.UUID) -> DocumentPrecheck | None:
        parsed_id = _parse_uuid(document_id)
        if parsed_id is None:
            return None
        return (
            self.db.query(DocumentPrecheck)
            .filter(
                DocumentPrecheck.document_id == parsed_id,
                DocumentPrecheck.is_latest.is_(True),
            )
            .order_by(desc(DocumentPrecheck.created_at))
            .first()
        )

    def list_for_case(
        self,
        case_id: str | uuid.UUID,
        *,
        document_id: str | uuid.UUID | None = None,
        latest: bool = True,
    ) -> list[DocumentPrecheck]:
        parsed_case_id = _parse_uuid(case_id)
        query = self.db.query(DocumentPrecheck).filter(DocumentPrecheck.case_id == parsed_case_id)
        if document_id is not None:
            parsed_document_id = _parse_uuid(document_id)
            query = query.filter(DocumentPrecheck.document_id == parsed_document_id)
        if latest:
            query = query.filter(DocumentPrecheck.is_latest.is_(True))
        return query.order_by(desc(DocumentPrecheck.created_at)).all()

    def unset_latest_for_document(self, document_id: uuid.UUID) -> None:
        (
            self.db.query(DocumentPrecheck)
            .filter(DocumentPrecheck.document_id == document_id)
            .update({"is_latest": False}, synchronize_session=False)
        )
        self.db.flush()

    def create_precheck(self, **values: Any) -> DocumentPrecheck:
        item = DocumentPrecheck(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_ocr_job(self, **values: Any) -> OcrJob:
        item = OcrJob(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_ocr_job(self, document_id: str | uuid.UUID) -> OcrJob | None:
        parsed_id = _parse_uuid(document_id)
        if parsed_id is None:
            return None
        return (
            self.db.query(OcrJob)
            .filter(OcrJob.document_id == parsed_id)
            .order_by(desc(OcrJob.created_at))
            .first()
        )

    def replace_ocr_pages(self, ocr_job_id: uuid.UUID, pages: list[dict[str, Any]]) -> list[OcrPageResult]:
        self.db.query(OcrPageResult).filter(OcrPageResult.ocr_job_id == ocr_job_id).delete()
        created: list[OcrPageResult] = []
        for page in pages:
            item = OcrPageResult(ocr_job_id=ocr_job_id, **page)
            self.db.add(item)
            created.append(item)
        self.db.flush()
        return created

    def replace_issues(
        self,
        precheck_id: uuid.UUID,
        document_id: uuid.UUID,
        issues: list[dict[str, Any]],
    ) -> list[DocumentIssue]:
        self.db.query(DocumentIssue).filter(DocumentIssue.document_precheck_id == precheck_id).delete()
        created: list[DocumentIssue] = []
        for issue in issues:
            item = DocumentIssue(
                document_precheck_id=precheck_id,
                document_id=document_id,
                page_number=issue.get("page_number"),
                severity=issue["severity"],
                code=issue["code"],
                title=issue["title"],
                message=issue["message"],
                suggested_action=issue.get("suggested_action"),
                metadata_json=issue.get("metadata"),
            )
            self.db.add(item)
            created.append(item)
        self.db.flush()
        return created

    def create_ai_confidence(self, **values: Any) -> AiConfidence:
        item = AiConfidence(**values)
        self.db.add(item)
        self.db.flush()
        return item


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
