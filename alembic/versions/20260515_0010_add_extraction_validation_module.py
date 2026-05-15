"""add extraction validation module

Revision ID: 20260515_0010
Revises: 20260515_0009
Create Date: 2026-05-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260515_0010"
down_revision: Union[str, Sequence[str], None] = "20260515_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confirmation_status", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("ai_provider", sa.String(length=60), nullable=True),
        sa.Column("ai_model", sa.String(length=120), nullable=True),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("questionnaire_response_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confidence_avg", sa.Numeric(5, 4), nullable=True),
        sa.Column("low_confidence_count", sa.Integer(), nullable=False),
        sa.Column("issues_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_extraction_runs_case", "extraction_runs", ["case_id"], unique=False)
    op.create_index(
        "idx_extraction_runs_case_status",
        "extraction_runs",
        ["case_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_extraction_runs_case_confirmation",
        "extraction_runs",
        ["case_id", "confirmation_status"],
        unique=False,
    )
    op.create_index("idx_extraction_runs_created_at", "extraction_runs", ["created_at"], unique=False)

    op.create_table(
        "employers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("raw_name", sa.String(length=220), nullable=True),
        sa.Column("nit", sa.String(length=40), nullable=True),
        sa.Column("employer_type", sa.String(length=30), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_employers_case", "employers", ["case_id"], unique=False)
    op.create_index("idx_employers_case_name", "employers", ["case_id", "name"], unique=False)
    op.create_index("idx_employers_status", "employers", ["status"], unique=False)

    op.create_table(
        "labor_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("period_type", sa.String(length=30), nullable=False),
        sa.Column("regime_hint", sa.String(length=30), nullable=True),
        sa.Column("weeks_detected", sa.Numeric(8, 2), nullable=True),
        sa.Column("days_detected", sa.Integer(), nullable=True),
        sa.Column("salary_base_detected", sa.Numeric(14, 2), nullable=True),
        sa.Column("novelty", sa.String(length=160), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_labor_periods_case", "labor_periods", ["case_id"], unique=False)
    op.create_index("idx_labor_periods_employer", "labor_periods", ["employer_id"], unique=False)
    op.create_index(
        "idx_labor_periods_dates",
        "labor_periods",
        ["case_id", "start_date", "end_date"],
        unique=False,
    )
    op.create_index("idx_labor_periods_status", "labor_periods", ["status"], unique=False)

    op.create_table(
        "extraction_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field_key", sa.String(length=120), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.JSON(), nullable=True),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_bbox", sa.JSON(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.String(length=30), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_extraction_fields_case", "extraction_fields", ["case_id"], unique=False)
    op.create_index("idx_extraction_fields_run", "extraction_fields", ["extraction_run_id"], unique=False)
    op.create_index(
        "idx_extraction_fields_entity",
        "extraction_fields",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index("idx_extraction_fields_status", "extraction_fields", ["status"], unique=False)
    op.create_index(
        "idx_extraction_fields_document",
        "extraction_fields",
        ["source_document_id"],
        unique=False,
    )

    op.create_table(
        "contribution_weeks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("labor_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=True),
        sa.Column("weeks", sa.Numeric(8, 2), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["labor_period_id"], ["labor_periods.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_contribution_weeks_case", "contribution_weeks", ["case_id"], unique=False)
    op.create_index(
        "idx_contribution_weeks_period",
        "contribution_weeks",
        ["labor_period_id"],
        unique=False,
    )
    op.create_index(
        "idx_contribution_weeks_employer",
        "contribution_weeks",
        ["employer_id"],
        unique=False,
    )
    op.create_index(
        "idx_contribution_weeks_year_month",
        "contribution_weeks",
        ["case_id", "year", "month"],
        unique=False,
    )

    op.create_table(
        "salary_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("labor_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("raw_value", sa.String(length=120), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["labor_period_id"], ["labor_periods.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_salary_bases_case", "salary_bases", ["case_id"], unique=False)
    op.create_index("idx_salary_bases_period", "salary_bases", ["labor_period_id"], unique=False)
    op.create_index("idx_salary_bases_employer", "salary_bases", ["employer_id"], unique=False)
    op.create_index(
        "idx_salary_bases_year_month",
        "salary_bases",
        ["case_id", "period_year", "period_month"],
        unique=False,
    )

    op.create_table(
        "contribution_gaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("gap_type", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_contribution_gaps_case", "contribution_gaps", ["case_id"], unique=False)
    op.create_index(
        "idx_contribution_gaps_dates",
        "contribution_gaps",
        ["case_id", "start_date", "end_date"],
        unique=False,
    )
    op.create_index("idx_contribution_gaps_status", "contribution_gaps", ["status"], unique=False)

    op.create_table(
        "labor_novelties",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("labor_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("novelty_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("detected_by", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["labor_period_id"], ["labor_periods.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_labor_novelties_case", "labor_novelties", ["case_id"], unique=False)
    op.create_index("idx_labor_novelties_period", "labor_novelties", ["labor_period_id"], unique=False)
    op.create_index("idx_labor_novelties_status", "labor_novelties", ["status"], unique=False)

    op.create_table(
        "user_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extraction_field_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field_key", sa.String(length=120), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("corrected_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correction_source", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corrected_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["extraction_field_id"], ["extraction_fields.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_user_corrections_case_created",
        "user_corrections",
        ["case_id", "created_at"],
        unique=False,
    )
    op.create_index("idx_user_corrections_field", "user_corrections", ["extraction_field_id"], unique=False)
    op.create_index(
        "idx_user_corrections_entity",
        "user_corrections",
        ["entity_type", "entity_id"],
        unique=False,
    )

    op.create_table(
        "extraction_confirmations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmation_status", sa.String(length=50), nullable=False),
        sa.Column("pending_fields_count", sa.Integer(), nullable=False),
        sa.Column("accepted_low_confidence_fields", sa.Boolean(), nullable=False),
        sa.Column("user_statement", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_extraction_confirmations_case", "extraction_confirmations", ["case_id"], unique=False)
    op.create_index("idx_extraction_confirmations_run", "extraction_confirmations", ["extraction_run_id"], unique=False)

    op.create_table(
        "extraction_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extraction_field_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issue_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_field_id"], ["extraction_fields.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_extraction_issues_case", "extraction_issues", ["case_id"], unique=False)
    op.create_index("idx_extraction_issues_run", "extraction_issues", ["extraction_run_id"], unique=False)
    op.create_index("idx_extraction_issues_field", "extraction_issues", ["extraction_field_id"], unique=False)
    op.create_index("idx_extraction_issues_status", "extraction_issues", ["status"], unique=False)

    op.create_table(
        "extraction_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_name", sa.String(length=120), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_state", sa.JSON(), nullable=True),
        sa.Column("new_state", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_extraction_audit_events_case_created",
        "extraction_audit_events",
        ["case_id", "created_at"],
        unique=False,
    )
    op.create_index("idx_extraction_audit_events_name", "extraction_audit_events", ["event_name"], unique=False)
    op.create_index(
        "idx_extraction_audit_events_entity",
        "extraction_audit_events",
        ["entity_type", "entity_id"],
        unique=False,
    )

    op.create_table(
        "extraction_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Numeric(5, 2), nullable=False),
        sa.Column("logs", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_extraction_jobs_idempotency_key"),
    )
    op.create_index("idx_extraction_jobs_case", "extraction_jobs", ["case_id"], unique=False)
    op.create_index("idx_extraction_jobs_run", "extraction_jobs", ["extraction_run_id"], unique=False)
    op.create_index("idx_extraction_jobs_status", "extraction_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_extraction_jobs_status", table_name="extraction_jobs")
    op.drop_index("idx_extraction_jobs_run", table_name="extraction_jobs")
    op.drop_index("idx_extraction_jobs_case", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
    op.drop_index("idx_extraction_audit_events_entity", table_name="extraction_audit_events")
    op.drop_index("idx_extraction_audit_events_name", table_name="extraction_audit_events")
    op.drop_index("idx_extraction_audit_events_case_created", table_name="extraction_audit_events")
    op.drop_table("extraction_audit_events")
    op.drop_index("idx_extraction_issues_status", table_name="extraction_issues")
    op.drop_index("idx_extraction_issues_field", table_name="extraction_issues")
    op.drop_index("idx_extraction_issues_run", table_name="extraction_issues")
    op.drop_index("idx_extraction_issues_case", table_name="extraction_issues")
    op.drop_table("extraction_issues")
    op.drop_index("idx_extraction_confirmations_run", table_name="extraction_confirmations")
    op.drop_index("idx_extraction_confirmations_case", table_name="extraction_confirmations")
    op.drop_table("extraction_confirmations")
    op.drop_index("idx_user_corrections_entity", table_name="user_corrections")
    op.drop_index("idx_user_corrections_field", table_name="user_corrections")
    op.drop_index("idx_user_corrections_case_created", table_name="user_corrections")
    op.drop_table("user_corrections")
    op.drop_index("idx_labor_novelties_status", table_name="labor_novelties")
    op.drop_index("idx_labor_novelties_period", table_name="labor_novelties")
    op.drop_index("idx_labor_novelties_case", table_name="labor_novelties")
    op.drop_table("labor_novelties")
    op.drop_index("idx_contribution_gaps_status", table_name="contribution_gaps")
    op.drop_index("idx_contribution_gaps_dates", table_name="contribution_gaps")
    op.drop_index("idx_contribution_gaps_case", table_name="contribution_gaps")
    op.drop_table("contribution_gaps")
    op.drop_index("idx_salary_bases_year_month", table_name="salary_bases")
    op.drop_index("idx_salary_bases_employer", table_name="salary_bases")
    op.drop_index("idx_salary_bases_period", table_name="salary_bases")
    op.drop_index("idx_salary_bases_case", table_name="salary_bases")
    op.drop_table("salary_bases")
    op.drop_index("idx_contribution_weeks_year_month", table_name="contribution_weeks")
    op.drop_index("idx_contribution_weeks_employer", table_name="contribution_weeks")
    op.drop_index("idx_contribution_weeks_period", table_name="contribution_weeks")
    op.drop_index("idx_contribution_weeks_case", table_name="contribution_weeks")
    op.drop_table("contribution_weeks")
    op.drop_index("idx_extraction_fields_document", table_name="extraction_fields")
    op.drop_index("idx_extraction_fields_status", table_name="extraction_fields")
    op.drop_index("idx_extraction_fields_entity", table_name="extraction_fields")
    op.drop_index("idx_extraction_fields_run", table_name="extraction_fields")
    op.drop_index("idx_extraction_fields_case", table_name="extraction_fields")
    op.drop_table("extraction_fields")
    op.drop_index("idx_labor_periods_status", table_name="labor_periods")
    op.drop_index("idx_labor_periods_dates", table_name="labor_periods")
    op.drop_index("idx_labor_periods_employer", table_name="labor_periods")
    op.drop_index("idx_labor_periods_case", table_name="labor_periods")
    op.drop_table("labor_periods")
    op.drop_index("idx_employers_status", table_name="employers")
    op.drop_index("idx_employers_case_name", table_name="employers")
    op.drop_index("idx_employers_case", table_name="employers")
    op.drop_table("employers")
    op.drop_index("idx_extraction_runs_created_at", table_name="extraction_runs")
    op.drop_index("idx_extraction_runs_case_confirmation", table_name="extraction_runs")
    op.drop_index("idx_extraction_runs_case_status", table_name="extraction_runs")
    op.drop_index("idx_extraction_runs_case", table_name="extraction_runs")
    op.drop_table("extraction_runs")
