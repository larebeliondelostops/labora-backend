import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.document import (
    Document,
    DocumentHash,
    DocumentPage,
    DocumentType,
    DocumentValidation,
    FileUpload,
)


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, document_id: str | uuid.UUID) -> Document | None:
        parsed_id = _parse_uuid(document_id)
        if parsed_id is None:
            return None
        return self.db.get(Document, parsed_id)

    def get_upload(self, upload_id: str | uuid.UUID) -> FileUpload | None:
        parsed_id = _parse_uuid(upload_id)
        if parsed_id is None:
            return None
        return self.db.get(FileUpload, parsed_id)

    def get_type_by_code(self, code: str) -> DocumentType | None:
        return (
            self.db.query(DocumentType)
            .filter(DocumentType.code == code, DocumentType.active.is_(True))
            .one_or_none()
        )

    def list_document_types(self, *, active_only: bool = True) -> list[DocumentType]:
        query = self.db.query(DocumentType)
        if active_only:
            query = query.filter(DocumentType.active.is_(True))
        return query.order_by(asc(DocumentType.sort_order), asc(DocumentType.name)).all()

    def create_document_type(self, **values: Any) -> DocumentType:
        item = DocumentType(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_document(self, **values: Any) -> Document:
        document = Document(**values)
        self.db.add(document)
        self.db.flush()
        return document

    def create_upload(self, **values: Any) -> FileUpload:
        upload = FileUpload(**values)
        self.db.add(upload)
        self.db.flush()
        return upload

    def list_case_documents(
        self,
        *,
        case_id: uuid.UUID,
        document_type_code: str | None,
        status: str | None,
        include_deleted: bool,
        page: int,
        limit: int,
    ) -> tuple[list[Document], int]:
        query = (
            self.db.query(Document)
            .outerjoin(DocumentType, Document.document_type_id == DocumentType.id)
            .filter(Document.case_id == case_id)
        )
        if not include_deleted:
            query = query.filter(Document.deleted_at.is_(None), Document.status != "deleted")
        if document_type_code:
            query = query.filter(DocumentType.code == document_type_code)
        if status:
            query = query.filter(Document.status == status)
        total = query.with_entities(func.count(Document.id)).scalar() or 0
        items = (
            query.order_by(desc(Document.created_at))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return items, total

    def list_active_case_documents(self, case_id: uuid.UUID) -> list[Document]:
        return (
            self.db.query(Document)
            .filter(
                Document.case_id == case_id,
                Document.deleted_at.is_(None),
                Document.status.notin_(["deleted", "replaced"]),
            )
            .order_by(asc(Document.created_at))
            .all()
        )

    def count_active_primary_documents(
        self,
        *,
        case_id: uuid.UUID,
        exclude_document_id: uuid.UUID | None = None,
    ) -> int:
        query = self.db.query(func.count(Document.id)).filter(
            Document.case_id == case_id,
            Document.deleted_at.is_(None),
            Document.status.notin_(["deleted", "replaced"]),
            Document.is_primary.is_(True),
        )
        if exclude_document_id is not None:
            query = query.filter(Document.id != exclude_document_id)
        return query.scalar() or 0

    def unset_other_primary_documents(self, *, case_id: uuid.UUID, keep_document_id: uuid.UUID) -> None:
        (
            self.db.query(Document)
            .filter(
                Document.case_id == case_id,
                Document.id != keep_document_id,
                Document.deleted_at.is_(None),
                Document.status.notin_(["deleted", "replaced"]),
                Document.is_primary.is_(True),
            )
            .update({"is_primary": False}, synchronize_session=False)
        )
        self.db.flush()

    def sum_active_case_size_bytes(self, case_id: uuid.UUID) -> int:
        total = (
            self.db.query(func.coalesce(func.sum(Document.size_bytes), 0))
            .filter(
                Document.case_id == case_id,
                Document.deleted_at.is_(None),
                Document.status.notin_(["deleted", "replaced"]),
            )
            .scalar()
        )
        return int(total or 0)

    def find_duplicate_by_hash(
        self,
        *,
        sha256_hash: str,
        exclude_document_id: uuid.UUID,
    ) -> Document | None:
        return (
            self.db.query(Document)
            .filter(
                Document.sha256_hash == sha256_hash,
                Document.id != exclude_document_id,
                Document.deleted_at.is_(None),
                Document.status.notin_(["deleted", "failed"]),
            )
            .order_by(asc(Document.created_at))
            .first()
        )

    def upsert_hash(
        self,
        *,
        document_id: uuid.UUID,
        hash_type: str,
        hash_value: str,
    ) -> DocumentHash:
        existing = (
            self.db.query(DocumentHash)
            .filter(
                DocumentHash.document_id == document_id,
                DocumentHash.hash_type == hash_type,
            )
            .one_or_none()
        )
        if existing is not None:
            existing.hash_value = hash_value
            return existing
        item = DocumentHash(
            document_id=document_id,
            hash_type=hash_type,
            hash_value=hash_value,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def replace_pages(
        self,
        *,
        document_id: uuid.UUID,
        pages: list[dict[str, Any]],
    ) -> list[DocumentPage]:
        self.db.query(DocumentPage).filter(DocumentPage.document_id == document_id).delete()
        created: list[DocumentPage] = []
        for page in pages:
            item = DocumentPage(document_id=document_id, **page)
            self.db.add(item)
            created.append(item)
        self.db.flush()
        return created

    def create_validation(self, **values: Any) -> DocumentValidation:
        validation = DocumentValidation(**values)
        self.db.add(validation)
        self.db.flush()
        return validation

    def latest_validation(self, document_id: uuid.UUID) -> DocumentValidation | None:
        return (
            self.db.query(DocumentValidation)
            .filter(DocumentValidation.document_id == document_id)
            .order_by(desc(DocumentValidation.created_at))
            .first()
        )

    def latest_upload_for_document(self, document_id: uuid.UUID) -> FileUpload | None:
        return (
            self.db.query(FileUpload)
            .filter(FileUpload.document_id == document_id)
            .order_by(desc(FileUpload.created_at))
            .first()
        )

    def mark_upload_failed(
        self,
        upload: FileUpload,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        upload.status = "failed"
        upload.error_code = error_code
        upload.error_message = error_message
        self.db.flush()

    def complete_upload(self, upload: FileUpload, completed_at: datetime) -> None:
        upload.status = "completed"
        upload.completed_at = completed_at
        self.db.flush()


def _parse_uuid(value: str | uuid.UUID) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
