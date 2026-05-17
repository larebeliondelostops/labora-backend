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


class FullAnalysis(Base):
    __tablename__ = "full_analysis"
    __table_args__ = (
        Index("idx_full_analysis_case_created", "case_id", "created_at"),
        Index("idx_full_analysis_case_status", "case_id", "status"),
        Index("idx_full_analysis_user_status", "user_id", "status"),
        Index("idx_full_analysis_case_version", "case_id", "version"),
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
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    triggered_by_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    input_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    executive_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    legal_conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_route: Mapped[str | None] = mapped_column(String(80), nullable=True)
    viability_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence_global: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    requires_human_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    human_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    rule_results: Mapped[list["LegalRuleResult"]] = relationship(
        back_populates="full_analysis",
        cascade="all, delete-orphan",
    )
    calculation_results: Mapped[list["CalculationResult"]] = relationship(
        back_populates="full_analysis",
        cascade="all, delete-orphan",
    )
    scenarios: Mapped[list["Scenario"]] = relationship(
        back_populates="full_analysis",
        cascade="all, delete-orphan",
    )
    confidence_scores: Mapped[list["ConfidenceScore"]] = relationship(
        back_populates="full_analysis",
        cascade="all, delete-orphan",
    )
    inconsistencies: Mapped[list["AnalysisInconsistency"]] = relationship(
        back_populates="full_analysis",
        cascade="all, delete-orphan",
    )
    jobs: Mapped[list["FullAnalysisJob"]] = relationship(
        back_populates="full_analysis",
        cascade="all, delete-orphan",
    )


class LegalRuleResult(Base):
    __tablename__ = "legal_rule_results"
    __table_args__ = (
        Index("idx_legal_rule_results_analysis", "full_analysis_id"),
        Index("idx_legal_rule_results_case_category", "case_id", "rule_category"),
        Index("idx_legal_rule_results_case_result", "case_id", "result"),
        Index("idx_legal_rule_results_requires_review", "requires_review"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_code: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(180), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_category: Mapped[str] = mapped_column(String(80), nullable=False)
    input_facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    condition_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    result_detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    full_analysis: Mapped[FullAnalysis] = relationship(back_populates="rule_results")


class CalculationResult(Base):
    __tablename__ = "calculation_results"
    __table_args__ = (
        Index("idx_calculation_results_analysis", "full_analysis_id"),
        Index("idx_calculation_results_case_type", "case_id", "calculation_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    calculation_code: Mapped[str] = mapped_column(String(120), nullable=False)
    calculation_name: Mapped[str] = mapped_column(String(180), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    calculation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    input_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    formula_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    formula_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    result_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    result_unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    full_analysis: Mapped[FullAnalysis] = relationship(back_populates="calculation_results")


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        Index("idx_scenarios_analysis", "full_analysis_id"),
        Index("idx_scenarios_case_type", "case_id", "scenario_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scenario_type: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    base_periods: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    legal_basis_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    calculation_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    amount_estimated: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    weeks_estimated: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    retroactive_estimated: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    difference_vs_recognized: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    full_analysis: Mapped[FullAnalysis] = relationship(back_populates="scenarios")


class ConfidenceScore(Base):
    __tablename__ = "confidence_scores"
    __table_args__ = (
        Index("idx_confidence_scores_analysis", "full_analysis_id"),
        Index("idx_confidence_scores_case_scope", "case_id", "scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    recommended_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    full_analysis: Mapped[FullAnalysis] = relationship(back_populates="confidence_scores")


class AnalysisInconsistency(Base):
    __tablename__ = "analysis_inconsistencies"
    __table_args__ = (
        Index("idx_analysis_inconsistencies_analysis", "full_analysis_id"),
        Index("idx_analysis_inconsistencies_case_type", "case_id", "inconsistency_type"),
        Index("idx_analysis_inconsistencies_severity", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inconsistency_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    legal_rule_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    calculation_refs: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    economic_impact_estimated: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    legal_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_documents: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    full_analysis: Mapped[FullAnalysis] = relationship(back_populates="inconsistencies")


class FullAnalysisJob(Base):
    __tablename__ = "full_analysis_jobs"
    __table_args__ = (
        Index("idx_full_analysis_jobs_analysis", "full_analysis_id"),
        Index("idx_full_analysis_jobs_case_status", "case_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    full_analysis: Mapped[FullAnalysis] = relationship(back_populates="jobs")
