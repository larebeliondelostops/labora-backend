import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.utils.dates import utc_now


class PreAnalysis(Base):
    __tablename__ = "pre_analysis"
    __table_args__ = (
        Index("idx_pre_analysis_case_created", "case_id", "created_at"),
        Index("idx_pre_analysis_case_status", "case_id", "status"),
        Index("idx_pre_analysis_user_status", "user_id", "status"),
        Index("idx_pre_analysis_input_hash", "case_id", "input_hash"),
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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    blocked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    output_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preliminary_case_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    traffic_light: Mapped[str | None] = mapped_column(String(16), nullable=True)
    viability_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completion_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    limited_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_detected_title: Mapped[str | None] = mapped_column(String(180), nullable=True)
    value_detected_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    issues: Mapped[list["PreIssue"]] = relationship(
        back_populates="pre_analysis",
        cascade="all, delete-orphan",
        order_by="PreIssue.sort_order",
    )
    viability: Mapped["PreViability | None"] = relationship(
        back_populates="pre_analysis",
        cascade="all, delete-orphan",
        uselist=False,
    )
    missing_documents: Mapped[list["MissingDocument"]] = relationship(
        back_populates="pre_analysis",
        cascade="all, delete-orphan",
    )
    case_signals: Mapped[list["CaseSignal"]] = relationship(
        back_populates="pre_analysis",
        cascade="all, delete-orphan",
    )
    jobs: Mapped[list["PreAnalysisJob"]] = relationship(
        back_populates="pre_analysis",
        cascade="all, delete-orphan",
    )


class PreIssue(Base):
    __tablename__ = "pre_issue"
    __table_args__ = (
        Index("idx_pre_issue_pre_analysis", "pre_analysis_id"),
        Index("idx_pre_issue_case", "case_id"),
        Index("idx_pre_issue_type", "issue_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    pre_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pre_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    public_summary: Mapped[str] = mapped_column(Text, nullable=False)
    locked_detail_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    evidence_label: Mapped[str | None] = mapped_column(String(180), nullable=True)
    evidence_refs: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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

    pre_analysis: Mapped[PreAnalysis] = relationship(back_populates="issues")


class PreViability(Base):
    __tablename__ = "pre_viability"
    __table_args__ = (
        Index("idx_pre_viability_pre_analysis", "pre_analysis_id"),
        Index("idx_pre_viability_case", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    pre_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pre_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    traffic_light: Mapped[str] = mapped_column(String(16), nullable=False)
    short_reason: Mapped[str] = mapped_column(Text, nullable=False)
    public_recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
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

    pre_analysis: Mapped[PreAnalysis] = relationship(back_populates="viability")


class MissingDocument(Base):
    __tablename__ = "missing_document"
    __table_args__ = (
        Index("idx_missing_document_case_status", "case_id", "status"),
        Index("idx_missing_document_pre_analysis", "pre_analysis_id"),
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
    pre_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pre_analysis.id", ondelete="CASCADE"),
        nullable=True,
    )
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    upload_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    pre_analysis: Mapped[PreAnalysis | None] = relationship(back_populates="missing_documents")


class CaseSignal(Base):
    __tablename__ = "case_signal"
    __table_args__ = (
        Index("idx_case_signal_case_visible", "case_id", "is_visible_to_user"),
        Index("idx_case_signal_pre_analysis", "pre_analysis_id"),
        Index("idx_case_signal_type", "signal_type"),
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
    pre_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pre_analysis.id", ondelete="CASCADE"),
        nullable=True,
    )
    signal_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    public_summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_refs: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    is_visible_to_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    pre_analysis: Mapped[PreAnalysis | None] = relationship(back_populates="case_signals")


class PreAnalysisJob(Base):
    __tablename__ = "pre_analysis_job"
    __table_args__ = (
        Index("idx_pre_analysis_job_pre_analysis", "pre_analysis_id"),
        Index("idx_pre_analysis_job_case_status", "case_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    pre_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pre_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    progress: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    current_step: Mapped[str | None] = mapped_column(String(180), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pre_analysis: Mapped[PreAnalysis] = relationship(back_populates="jobs")
