import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
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


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"
    __table_args__ = (
        Index("idx_document_extractions_case_status", "case_id", "status"),
        Index("idx_document_extractions_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extraction_engine: Mapped[str] = mapped_column(String(40), nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    extracted_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_text_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PensionAffiliateProfile(Base):
    __tablename__ = "pension_affiliate_profiles"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_pension_affiliate_profiles_case"),
        Index("idx_pension_affiliate_profiles_case", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sex: Mapped[str] = mapped_column(String(12), nullable=False, default="UNKNOWN")
    report_generated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fund_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    inferred_regime: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    current_weeks: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    current_individual_account_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    current_monthly_income_reference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    has_pension_bond: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pension_bond_estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    data_confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0"))
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class PensionMonthlyContribution(Base):
    __tablename__ = "pension_monthly_contributions"
    __table_args__ = (
        Index("idx_pension_contributions_case_period", "case_id", "period"),
        Index("idx_pension_contributions_source_document", "source_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    contributor_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    contributor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    employment_type: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    ibc: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    mandatory_contribution: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    days_contributed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weeks_calculated: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="normal")
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class PensionLegalParameter(Base):
    __tablename__ = "pension_legal_parameters"
    __table_args__ = (
        UniqueConstraint("year", "country", name="uq_pension_legal_parameters_year_country"),
        Index("idx_pension_legal_parameters_year", "year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="CO")
    smmlv: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ibc_min_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ibc_max_smmlv: Mapped[int] = mapped_column(Integer, nullable=False)
    ibc_max_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    rais_minimum_guarantee_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    rais_minimum_guarantee_age_male: Mapped[int] = mapped_column(Integer, nullable=False)
    rais_minimum_guarantee_age_female: Mapped[int] = mapped_column(Integer, nullable=False)
    early_retirement_threshold_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    source_label: Mapped[str | None] = mapped_column(String(180), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PensionSimulationAssumptions(Base):
    __tablename__ = "pension_simulation_assumptions"
    __table_args__ = (Index("idx_pension_assumptions_case_created", "case_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    target_age: Mapped[int] = mapped_column(Integer, nullable=False)
    income_reference_method: Mapped[str] = mapped_column(String(40), nullable=False)
    user_income_input: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    real_return_annual_conservative: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    real_return_annual_base: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    real_return_annual_optimistic: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    real_income_growth_annual: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    individual_account_rate: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    annuity_factor_method: Mapped[str] = mapped_column(String(40), nullable=False)
    expected_payment_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actuarial_table_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pension_bond_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    spouse_or_beneficiaries_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    monthly_expense_expected: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    target_monthly_pension: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class PensionSimulation(Base):
    __tablename__ = "pension_simulations"
    __table_args__ = (
        Index("idx_pension_simulations_case_created", "case_id", "created_at"),
        Index("idx_pension_simulations_case_status", "case_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    assumptions_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pension_simulation_assumptions.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    calculation_version: Mapped[str] = mapped_column(String(40), nullable=False)
    disclaimer_version: Mapped[str] = mapped_column(String(40), nullable=False)
    overall_diagnosis: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    visible_before_payment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assumptions: Mapped[PensionSimulationAssumptions] = relationship()
    scenarios: Mapped[list["PensionSimulationScenario"]] = relationship(
        back_populates="simulation",
        cascade="all, delete-orphan",
    )


class PensionSimulationScenario(Base):
    __tablename__ = "pension_simulation_scenarios"
    __table_args__ = (
        UniqueConstraint("simulation_id", "scenario", name="uq_pension_simulation_scenario"),
        Index("idx_pension_scenarios_simulation", "simulation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    simulation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("pension_simulations.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario: Mapped[str] = mapped_column(String(20), nullable=False)
    projected_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    projected_weeks: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    projected_monthly_pension_today_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    projected_monthly_pension_nominal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    replacement_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    smmlv_multiple: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    capital_gap: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    additional_savings_required: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    can_retire_by_capital: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qualifies_minimum_guarantee: Mapped[bool] = mapped_column(Boolean, nullable=False)
    alerts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    formula_trace: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    simulation: Mapped[PensionSimulation] = relationship(back_populates="scenarios")


class PensionAlert(Base):
    __tablename__ = "pension_alerts"
    __table_args__ = (
        Index("idx_pension_alerts_case_created", "case_id", "created_at"),
        Index("idx_pension_alerts_simulation", "simulation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    simulation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pension_simulations.id", ondelete="CASCADE"), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    action_suggested: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class LegalRoute(Base):
    __tablename__ = "legal_routes"
    __table_args__ = (
        Index("idx_legal_routes_case_created", "case_id", "id"),
        Index("idx_legal_routes_simulation", "simulation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    simulation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("pension_simulations.id", ondelete="SET NULL"), nullable=True)
    route_type: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    viability: Mapped[str] = mapped_column(String(20), nullable=False)
    requires_payment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suggested_template_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    required_documents: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class LegalTemplateCatalogItem(Base):
    __tablename__ = "legal_template_catalog"
    __table_args__ = (
        UniqueConstraint("template_key", name="uq_legal_template_catalog_key"),
        Index("idx_legal_template_catalog_active", "active"),
        Index("idx_legal_template_catalog_regime", "regime"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)
    process_type: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_professional_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CaseEntitlement(Base):
    __tablename__ = "case_entitlements"
    __table_args__ = (
        UniqueConstraint("case_id", "entitlement", name="uq_case_entitlement"),
        Index("idx_case_entitlements_case_active", "case_id", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    entitlement: Mapped[str] = mapped_column(String(80), nullable=False)
    unlocked_by_payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payments.id", ondelete="SET NULL"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

