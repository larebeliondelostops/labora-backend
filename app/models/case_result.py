import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.utils.dates import utc_now


class CaseResult(Base):
    __tablename__ = "case_result"
    __table_args__ = (
        Index("idx_case_result_case_version", "case_id", "version"),
        Index("idx_case_result_case_status", "case_id", "status"),
        Index("idx_case_result_visible", "case_id", "is_visible_to_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("full_analysis.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    result_type: Mapped[str] = mapped_column(String(60), nullable=False)
    final_viability_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    economic_estimate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    recommended_route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    headline: Mapped[str] = mapped_column(String(240), nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    main_inconsistency: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_disclaimer: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_visible_to_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    final_viability: Mapped["FinalViability | None"] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys="FinalViability.case_result_id",
    )
    economic_estimate: Mapped["EconomicEstimate | None"] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
        uselist=False,
    )
    recommended_route: Mapped["RecommendedRoute | None"] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
        uselist=False,
    )
    cards: Mapped[list["ResultCard"]] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
    )
    inconsistencies: Mapped[list["ResultInconsistency"]] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["ResultAuditEvent"]] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
    )


class FinalViability(Base):
    __tablename__ = "final_viability"
    __table_args__ = (
        Index("idx_final_viability_result", "case_result_id"),
        Index("idx_final_viability_level", "level"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_result.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    level: Mapped[str] = mapped_column(String(30), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    color: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    weaknesses: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_information: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    case_result: Mapped[CaseResult] = relationship(back_populates="final_viability")


class EconomicEstimate(Base):
    __tablename__ = "economic_estimate"
    __table_args__ = (
        Index("idx_economic_estimate_result", "case_result_id"),
        Index("idx_economic_estimate_has_estimate", "has_economic_estimate"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_result.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="COP")
    estimated_claimable_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    estimated_retroactive_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    estimated_monthly_difference: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    recognized_scenario_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    corrected_scenario_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    has_economic_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estimate_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    calculation_reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    assumptions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    case_result: Mapped[CaseResult] = relationship(back_populates="economic_estimate")


class RecommendedRoute(Base):
    __tablename__ = "recommended_route"
    __table_args__ = (
        Index("idx_recommended_route_result", "case_result_id"),
        Index("idx_recommended_route_type", "route_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_result.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route_type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    next_action_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    next_action_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    next_action_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requires_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_professional_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_generate_legal_action: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recommended_legal_action_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    blockers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_documents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    case_result: Mapped[CaseResult] = relationship(back_populates="recommended_route")


class ResultCard(Base):
    __tablename__ = "result_card"
    __table_args__ = (
        Index("idx_result_card_result_sort", "case_result_id", "sort_order"),
        Index("idx_result_card_key", "card_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_result.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    card_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    case_result: Mapped[CaseResult] = relationship(back_populates="cards")


class ResultInconsistency(Base):
    __tablename__ = "result_inconsistency"
    __table_args__ = (
        Index("idx_result_inconsistency_result_sort", "case_result_id", "sort_order"),
        Index("idx_result_inconsistency_type", "inconsistency_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_result.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inconsistency_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_impact: Mapped[str | None] = mapped_column(String(40), nullable=True)
    economic_impact: Mapped[str | None] = mapped_column(String(40), nullable=True)
    estimated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    source_document_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_references: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_documents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    case_result: Mapped[CaseResult] = relationship(back_populates="inconsistencies")


class ResultAuditEvent(Base):
    __tablename__ = "result_audit_event"
    __table_args__ = (
        Index("idx_result_audit_case_created", "case_id", "created_at"),
        Index("idx_result_audit_result_created", "case_result_id", "created_at"),
        Index("idx_result_audit_event_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_result.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    case_result: Mapped[CaseResult | None] = relationship(back_populates="audit_events")
