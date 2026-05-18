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
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.utils.dates import utc_now


class LegalAction(Base):
    __tablename__ = "legal_actions"
    __table_args__ = (
        Index("idx_legal_actions_case_id", "case_id"),
        Index("idx_legal_actions_user_id", "user_id"),
        Index("idx_legal_actions_status", "status"),
        Index("idx_legal_actions_type", "action_type"),
        Index(
            "uq_legal_actions_case_type_active",
            "case_id",
            "action_type",
            unique=True,
            postgresql_where=text("status != 'cancelled'"),
            sqlite_where=text("status != 'cancelled'"),
        ),
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="not_started")
    eligibility_status: Mapped[str] = mapped_column(String(50), nullable=False)
    eligibility_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    professional_review_level: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    warnings: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    pending_data: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    missing_attachments: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    selected_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
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
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    drafts: Mapped[list["LegalDraft"]] = relationship(
        back_populates="legal_action",
        cascade="all, delete-orphan",
    )


class LegalTemplate(Base):
    __tablename__ = "legal_templates"
    __table_args__ = (
        UniqueConstraint(
            "action_type",
            "legal_domain",
            "template_version",
            name="uq_legal_templates_action_domain_version",
        ),
        Index("idx_legal_templates_action_active", "action_type", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(80), nullable=True, default="Colombia")
    legal_domain: Mapped[str] = mapped_column(String(80), nullable=False, default="pensional")
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    required_inputs_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    required_attachments_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    drafts: Mapped[list["LegalDraft"]] = relationship(back_populates="template")


class LegalDraft(Base):
    __tablename__ = "legal_drafts"
    __table_args__ = (
        Index("idx_legal_drafts_action_id", "legal_action_id"),
        Index("idx_legal_drafts_case_id", "case_id"),
        Index("idx_legal_drafts_user_id", "user_id"),
        Index("idx_legal_drafts_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    legal_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="created")
    generation_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="template_only")
    document_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    user_inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ai_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    professional_review_level: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
    last_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    legal_action: Mapped[LegalAction] = relationship(back_populates="drafts")
    template: Mapped[LegalTemplate] = relationship(back_populates="drafts")
    sections: Mapped[list["DraftSection"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="DraftSection.order_index",
    )
    versions: Mapped[list["DraftVersion"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="DraftVersion.version_number",
    )
    comments: Mapped[list["DraftComment"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
    )
    quality_checks: Mapped[list["DraftQualityCheck"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
    )
    exports: Mapped[list["DraftExport"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
    )


class DraftSection(Base):
    __tablename__ = "draft_sections"
    __table_args__ = (
        UniqueConstraint("draft_id", "section_key", name="uq_draft_sections_key"),
        Index("idx_draft_sections_draft_order", "draft_id", "order_index"),
        Index("idx_draft_sections_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_plain: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    source_references: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    pending_markers: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    generated_by_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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

    draft: Mapped[LegalDraft] = relationship(back_populates="sections")


class DraftVersion(Base):
    __tablename__ = "draft_versions"
    __table_args__ = (
        UniqueConstraint("draft_id", "version_number", name="uq_draft_versions_number"),
        Index("idx_draft_versions_draft_created", "draft_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(String(40), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    draft: Mapped[LegalDraft] = relationship(back_populates="versions")


class DraftComment(Base):
    __tablename__ = "draft_comments"
    __table_args__ = (
        Index("idx_draft_comments_draft_status", "draft_id", "status"),
        Index("idx_draft_comments_section", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_sections.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    author_role: Mapped[str] = mapped_column(String(40), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    draft: Mapped[LegalDraft] = relationship(back_populates="comments")


class DraftQualityCheck(Base):
    __tablename__ = "draft_quality_checks"
    __table_args__ = (
        Index("idx_draft_quality_checks_draft_created", "draft_id", "created_at"),
        Index("idx_draft_quality_checks_status", "overall_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    overall_status: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    checks: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    critical_warnings: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    draft: Mapped[LegalDraft] = relationship(back_populates="quality_checks")


class DraftExport(Base):
    __tablename__ = "draft_exports"
    __table_args__ = (
        Index("idx_draft_exports_draft_status", "draft_id", "status"),
        Index("idx_draft_exports_case_format", "case_id", "format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    format: Mapped[str] = mapped_column(String(12), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    include_watermark: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
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

    draft: Mapped[LegalDraft] = relationship(back_populates="exports")


class AiGenerationRun(Base):
    __tablename__ = "ai_generation_runs"
    __table_args__ = (
        Index("idx_ai_generation_runs_case_status", "case_id", "status"),
        Index("idx_ai_generation_runs_draft", "draft_id"),
        Index("idx_ai_generation_runs_section", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="SET NULL"),
        nullable=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_sections.id", ondelete="SET NULL"),
        nullable=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    structured_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class LegalActionJob(Base):
    __tablename__ = "legal_action_jobs"
    __table_args__ = (
        Index("idx_legal_action_jobs_case_status", "case_id", "status"),
        Index("idx_legal_action_jobs_draft", "draft_id"),
        Index("idx_legal_action_jobs_type_status", "job_type", "status"),
        UniqueConstraint("idempotency_key", name="uq_legal_action_jobs_idempotency_key"),
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
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_drafts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_sections.id", ondelete="CASCADE"),
        nullable=True,
    )
    export_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_exports.id", ondelete="CASCADE"),
        nullable=True,
    )
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
