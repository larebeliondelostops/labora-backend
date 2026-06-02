"""add prepayment pension simulation flow

Revision ID: 20260601_0022
Revises: 20260519_0021
Create Date: 2026-06-01 17:00:00.000000
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260601_0022"
down_revision: Union[str, Sequence[str], None] = "20260519_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOCUMENT_TYPES = [
    ("pension_history_pdf", "Historia laboral AFP", "Historia laboral del fondo de pensiones en PDF.", "pension", True, True),
    ("individual_account_statement", "Extracto de cuenta individual", "Extracto de cuenta individual RAIS.", "pension", False, False),
    ("pension_bond_status", "Estado de bono pensional", "Soporte de bono pensional.", "pension", False, False),
    ("afp_projection", "Proyeccion oficial AFP", "Proyeccion de pension emitida por AFP.", "pension", False, False),
    ("pension_resolution", "Resolucion pensional", "Reconocimiento, reliquidacion, negacion o acto similar.", "legal", False, False),
    ("salary_certificate", "Certificacion salarial", "Certificacion de salario o factores salariales.", "labor", False, False),
    ("labor_certificate", "Certificacion laboral", "Certificacion laboral o tiempos de servicio.", "labor", False, False),
    ("id_document", "Documento de identidad", "Documento de identidad del afiliado o solicitante.", "identity", False, False),
    ("official_response", "Respuesta oficial", "Respuesta del fondo, Colpensiones o entidad.", "legal", False, False),
    ("payroll_stub", "Desprendible de pago", "Colilla o desprendible de pago.", "labor", False, False),
    ("administrative_act", "Acto administrativo", "Actos administrativos, nombramientos o actas.", "legal", False, False),
    ("previous_claim_or_lawsuit", "Reclamacion o demanda previa", "Reclamaciones, tutelas o demandas previas.", "legal", False, False),
    ("other", "Otro", "Documento complementario no clasificado.", "other", False, False),
]

TEMPLATES = [
    ("RAIS_RETIRO_PROGRAMADO", "RAIS retiro programado", "Borrador para revisar retiro programado o mesada baja.", "RAIS", "administrative_claim"),
    ("RAIS_RENTA_VITALICIA", "RAIS renta vitalicia", "Borrador para revisar renta vitalicia.", "RAIS", "administrative_claim"),
    ("RAIS_GPM", "RAIS garantia pension minima", "Borrador para garantia de pension minima.", "RAIS", "administrative_claim"),
    ("DEVOLUCION_SALDOS", "Devolucion de saldos", "Borrador para devolucion de saldos.", "RAIS", "administrative_claim"),
    ("BONO_PENSIONAL", "Bono pensional", "Borrador para correccion o reconocimiento de bono pensional.", "MIXED", "administrative_claim"),
]


def upgrade() -> None:
    _add_case_columns()
    _create_pension_tables()
    _seed_document_types()
    _seed_template_catalog()


def downgrade() -> None:
    op.drop_table("case_entitlements")
    op.drop_table("legal_template_catalog")
    op.drop_table("legal_routes")
    op.drop_table("pension_alerts")
    op.drop_table("pension_simulation_scenarios")
    op.drop_table("pension_simulations")
    op.drop_table("pension_simulation_assumptions")
    op.drop_table("pension_legal_parameters")
    op.drop_table("pension_monthly_contributions")
    op.drop_table("pension_affiliate_profiles")
    op.drop_table("document_extractions")
    for column_name in [
        "paid_legal_document_unlocked",
        "has_free_simulation",
        "current_situation",
        "case_goal",
        "pension_regime",
    ]:
        op.drop_column("cases", column_name)
    # Seeded document types are intentionally left in place on downgrade because
    # uploaded documents may reference them.


def _add_case_columns() -> None:
    op.add_column("cases", sa.Column("pension_regime", sa.String(length=20), nullable=False, server_default="UNKNOWN"))
    op.add_column("cases", sa.Column("case_goal", sa.String(length=60), nullable=False, server_default="unknown"))
    op.add_column("cases", sa.Column("current_situation", sa.String(length=60), nullable=False, server_default="unknown"))
    op.add_column("cases", sa.Column("has_free_simulation", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("cases", sa.Column("paid_legal_document_unlocked", sa.Boolean(), nullable=False, server_default=sa.false()))


def _create_pension_tables() -> None:
    op.create_table(
        "document_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_engine", sa.String(length=40), nullable=False),
        sa.Column("llm_provider", sa.String(length=40), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("extracted_json", sa.JSON(), nullable=False),
        sa.Column("raw_text_path", sa.String(length=500), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_document_extractions_case_status", "document_extractions", ["case_id", "status"])
    op.create_index("idx_document_extractions_document", "document_extractions", ["document_id"])

    op.create_table(
        "pension_affiliate_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.String(length=30), nullable=True),
        sa.Column("document_number", sa.String(length=80), nullable=True),
        sa.Column("full_name", sa.String(length=240), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("sex", sa.String(length=12), nullable=False),
        sa.Column("report_generated_at", sa.String(length=40), nullable=True),
        sa.Column("fund_name", sa.String(length=160), nullable=True),
        sa.Column("inferred_regime", sa.String(length=20), nullable=False),
        sa.Column("current_weeks", sa.Numeric(10, 2), nullable=True),
        sa.Column("current_individual_account_balance", sa.Numeric(16, 2), nullable=True),
        sa.Column("current_monthly_income_reference", sa.Numeric(14, 2), nullable=True),
        sa.Column("has_pension_bond", sa.Boolean(), nullable=False),
        sa.Column("pension_bond_estimated_value", sa.Numeric(16, 2), nullable=True),
        sa.Column("data_confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_pension_affiliate_profiles_case"),
    )
    op.create_index("idx_pension_affiliate_profiles_case", "pension_affiliate_profiles", ["case_id"])

    op.create_table(
        "pension_monthly_contributions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("contributor_name", sa.String(length=180), nullable=True),
        sa.Column("contributor_id", sa.String(length=80), nullable=True),
        sa.Column("employment_type", sa.String(length=20), nullable=False),
        sa.Column("ibc", sa.Numeric(14, 2), nullable=False),
        sa.Column("mandatory_contribution", sa.Numeric(14, 2), nullable=True),
        sa.Column("days_contributed", sa.Integer(), nullable=True),
        sa.Column("weeks_calculated", sa.Numeric(8, 2), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pension_contributions_case_period", "pension_monthly_contributions", ["case_id", "period"])
    op.create_index("idx_pension_contributions_source_document", "pension_monthly_contributions", ["source_document_id"])

    op.create_table(
        "pension_legal_parameters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("smmlv", sa.Numeric(14, 2), nullable=False),
        sa.Column("ibc_min_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("ibc_max_smmlv", sa.Integer(), nullable=False),
        sa.Column("ibc_max_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("rais_minimum_guarantee_weeks", sa.Integer(), nullable=False),
        sa.Column("rais_minimum_guarantee_age_male", sa.Integer(), nullable=False),
        sa.Column("rais_minimum_guarantee_age_female", sa.Integer(), nullable=False),
        sa.Column("early_retirement_threshold_multiplier", sa.Numeric(5, 2), nullable=True),
        sa.Column("source_label", sa.String(length=180), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year", "country", name="uq_pension_legal_parameters_year_country"),
    )
    op.create_index("idx_pension_legal_parameters_year", "pension_legal_parameters", ["year"])

    op.create_table(
        "pension_simulation_assumptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_age", sa.Integer(), nullable=False),
        sa.Column("income_reference_method", sa.String(length=40), nullable=False),
        sa.Column("user_income_input", sa.Numeric(14, 2), nullable=True),
        sa.Column("real_return_annual_conservative", sa.Numeric(7, 6), nullable=False),
        sa.Column("real_return_annual_base", sa.Numeric(7, 6), nullable=False),
        sa.Column("real_return_annual_optimistic", sa.Numeric(7, 6), nullable=False),
        sa.Column("real_income_growth_annual", sa.Numeric(7, 6), nullable=False),
        sa.Column("individual_account_rate", sa.Numeric(7, 6), nullable=False),
        sa.Column("annuity_factor_method", sa.String(length=40), nullable=False),
        sa.Column("expected_payment_years", sa.Integer(), nullable=True),
        sa.Column("actuarial_table_version", sa.String(length=80), nullable=True),
        sa.Column("pension_bond_value", sa.Numeric(16, 2), nullable=True),
        sa.Column("spouse_or_beneficiaries_info", sa.JSON(), nullable=True),
        sa.Column("monthly_expense_expected", sa.Numeric(14, 2), nullable=True),
        sa.Column("target_monthly_pension", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pension_assumptions_case_created", "pension_simulation_assumptions", ["case_id", "created_at"])

    op.create_table(
        "pension_simulations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assumptions_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("calculation_version", sa.String(length=40), nullable=False),
        sa.Column("disclaimer_version", sa.String(length=40), nullable=False),
        sa.Column("overall_diagnosis", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("visible_before_payment", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assumptions_id"], ["pension_simulation_assumptions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pension_simulations_case_created", "pension_simulations", ["case_id", "created_at"])
    op.create_index("idx_pension_simulations_case_status", "pension_simulations", ["case_id", "status"])

    op.create_table(
        "pension_simulation_scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario", sa.String(length=20), nullable=False),
        sa.Column("projected_balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("projected_weeks", sa.Numeric(10, 2), nullable=False),
        sa.Column("projected_monthly_pension_today_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("projected_monthly_pension_nominal", sa.Numeric(14, 2), nullable=True),
        sa.Column("replacement_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("smmlv_multiple", sa.Numeric(7, 4), nullable=False),
        sa.Column("capital_gap", sa.Numeric(16, 2), nullable=True),
        sa.Column("additional_savings_required", sa.Numeric(14, 2), nullable=True),
        sa.Column("can_retire_by_capital", sa.Boolean(), nullable=False),
        sa.Column("qualifies_minimum_guarantee", sa.Boolean(), nullable=False),
        sa.Column("alerts", sa.JSON(), nullable=False),
        sa.Column("formula_trace", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["simulation_id"], ["pension_simulations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("simulation_id", "scenario", name="uq_pension_simulation_scenario"),
    )
    op.create_index("idx_pension_scenarios_simulation", "pension_simulation_scenarios", ["simulation_id"])

    op.create_table(
        "pension_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("action_suggested", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["simulation_id"], ["pension_simulations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pension_alerts_case_created", "pension_alerts", ["case_id", "created_at"])
    op.create_index("idx_pension_alerts_simulation", "pension_alerts", ["simulation_id"])

    op.create_table(
        "legal_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("route_type", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("viability", sa.String(length=20), nullable=False),
        sa.Column("requires_payment", sa.Boolean(), nullable=False),
        sa.Column("suggested_template_key", sa.String(length=80), nullable=True),
        sa.Column("required_documents", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["simulation_id"], ["pension_simulations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_legal_routes_case_created", "legal_routes", ["case_id", "id"])
    op.create_index("idx_legal_routes_simulation", "legal_routes", ["simulation_id"])

    op.create_table(
        "legal_template_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("regime", sa.String(length=20), nullable=False),
        sa.Column("process_type", sa.String(length=50), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("requires_professional_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_key", name="uq_legal_template_catalog_key"),
    )
    op.create_index("idx_legal_template_catalog_active", "legal_template_catalog", ["active"])
    op.create_index("idx_legal_template_catalog_regime", "legal_template_catalog", ["regime"])

    op.create_table(
        "case_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entitlement", sa.String(length=80), nullable=False),
        sa.Column("unlocked_by_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unlocked_by_payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "entitlement", name="uq_case_entitlement"),
    )
    op.create_index("idx_case_entitlements_case_active", "case_entitlements", ["case_id", "active"])


def _seed_document_types() -> None:
    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    for index, (code, name, description, category, required, primary) in enumerate(DOCUMENT_TYPES, start=1):
        connection.execute(
            sa.text(
                """
                INSERT INTO document_types (
                    id, code, name, description, category, is_required_for_basic_flow,
                    is_primary_candidate, allowed_mime_types, max_size_mb, sort_order,
                    active, created_at, updated_at
                )
                SELECT :id, :code, :name, :description, :category, :required,
                       :primary, :allowed_mime_types, :max_size_mb, :sort_order,
                       true, :created_at, :updated_at
                WHERE NOT EXISTS (SELECT 1 FROM document_types WHERE code = :code)
                """
            ).bindparams(sa.bindparam("allowed_mime_types", type_=sa.JSON())),
            {
                "id": str(uuid.uuid4()),
                "code": code,
                "name": name,
                "description": description,
                "category": category,
                "required": required,
                "primary": primary,
                "allowed_mime_types": ["application/pdf", "image/jpeg", "image/png"] if code != "pension_history_pdf" else ["application/pdf"],
                "max_size_mb": 25,
                "sort_order": index,
                "created_at": now,
                "updated_at": now,
            },
        )


def _seed_template_catalog() -> None:
    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    for key, name, description, regime, process_type in TEMPLATES:
        connection.execute(
            sa.text(
                """
                INSERT INTO legal_template_catalog (
                    id, template_key, name, description, regime, process_type,
                    storage_path, active, requires_professional_review, created_at
                )
                SELECT :id, :template_key, :name, :description, :regime, :process_type,
                       :storage_path, true, true, :created_at
                WHERE NOT EXISTS (
                    SELECT 1 FROM legal_template_catalog WHERE template_key = :template_key
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "template_key": key,
                "name": name,
                "description": description,
                "regime": regime,
                "process_type": process_type,
                "storage_path": f"templates/{key}.docx",
                "created_at": now,
            },
        )
