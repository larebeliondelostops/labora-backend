import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.utils.dates import utc_now


class DocumentType(Base):
    __tablename__ = "document_types"
    __table_args__ = (
        Index("idx_document_types_category", "category"),
        Index("idx_document_types_active_sort", "active", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    is_required_for_basic_flow: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_primary_candidate: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    allowed_mime_types: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    max_size_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="document_type")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_case_status", "case_id", "status"),
        Index("idx_documents_case_created", "case_id", "created_at"),
        Index("idx_documents_sha256", "sha256_hash"),
        Index("idx_documents_deleted_at", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_types.id"),
        nullable=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    validation_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="not_started",
    )
    classification_source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="unknown",
    )
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )
    replaced_by_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_password_protected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_corrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    virus_scan_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    document_type: Mapped[DocumentType | None] = relationship(back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    uploads: Mapped[list["FileUpload"]] = relationship(back_populates="document")
    hashes: Mapped[list["DocumentHash"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    validations: Mapped[list["DocumentValidation"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),
        Index("idx_document_pages_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rotation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    text_extracted: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    warnings: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")


class FileUpload(Base):
    __tablename__ = "file_uploads"
    __table_args__ = (
        Index("idx_file_uploads_case_status", "case_id", "status"),
        Index("idx_file_uploads_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="initiated")
    upload_method: Mapped[str] = mapped_column(String(30), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    document: Mapped[Document | None] = relationship(back_populates="uploads")


class DocumentHash(Base):
    __tablename__ = "document_hashes"
    __table_args__ = (
        UniqueConstraint("document_id", "hash_type", name="uq_document_hash_type"),
        Index("idx_document_hashes_value", "hash_type", "hash_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    hash_type: Mapped[str] = mapped_column(String(30), nullable=False)
    hash_value: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    document: Mapped[Document] = relationship(back_populates="hashes")


class DocumentValidation(Base):
    __tablename__ = "document_validations"
    __table_args__ = (
        Index("idx_document_validations_document_created", "document_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    checks: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    warnings: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    errors: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    document: Mapped[Document] = relationship(back_populates="validations")
