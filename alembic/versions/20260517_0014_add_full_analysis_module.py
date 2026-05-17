"""add full analysis module

Revision ID: 20260517_0014
Revises: 20260517_0013
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260517_0014"
down_revision: Union[str, Sequence[str], None] = "20260517_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "full_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("triggered_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("triggered_by_role", sa.String(length=50), nullable=True),
        sa.Column("input_snapshot", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("executive_result", sa.JSON(), nullable=True),
        sa.Column("legal_conclusion", sa.Text(), nullable=True),
        sa.Column("recommended_route", sa.String(length=80), nullable=True),
        sa.Column("viability_level", sa.String(length=40), nullable=True),
        sa.Column("confidence_global", sa.Numeric(5, 2), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("human_review_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_full_analysis_case_created", "full_analysis", ["case_id", "created_at"], unique=False)
    op.create_index("idx_full_analysis_case_status", "full_analysis", ["case_id", "status"], unique=False)
    op.create_index("idx_full_analysis_case_version", "full_analysis", ["case_id", "version"], unique=False)
    op.create_index("idx_full_analysis_user_status", "full_analysis", ["user_id", "status"], unique=False)

    op.create_table(
        "legal_rule_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_code", sa.String(length=120), nullable=False),
        sa.Column("rule_name", sa.String(length=180), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("rule_category", sa.String(length=80), nullable=False),
        sa.Column("input_facts", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("condition_expression", sa.Text(), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("result_detail", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["full_analysis_id"], ["full_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_legal_rule_results_analysis", "legal_rule_results", ["full_analysis_id"], unique=False)
    op.create_index("idx_legal_rule_results_case_category", "legal_rule_results", ["case_id", "rule_category"], unique=False)
    op.create_index("idx_legal_rule_results_case_result", "legal_rule_results", ["case_id", "result"], unique=False)
    op.create_index("idx_legal_rule_results_requires_review", "legal_rule_results", ["requires_review"], unique=False)

    op.create_table(
        "calculation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("calculation_code", sa.String(length=120), nullable=False),
        sa.Column("calculation_name", sa.String(length=180), nullable=False),
        sa.Column("calculation_version", sa.String(length=32), nullable=False),
        sa.Column("calculation_type", sa.String(length=80), nullable=False),
        sa.Column("input_values", sa.JSON(), nullable=False),
        sa.Column("formula_ref", sa.String(length=120), nullable=True),
        sa.Column("formula_expression", sa.Text(), nullable=True),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("result_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("result_unit", sa.String(length=40), nullable=True),
        sa.Column("result_detail", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["full_analysis_id"], ["full_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_calculation_results_analysis", "calculation_results", ["full_analysis_id"], unique=False)
    op.create_index("idx_calculation_results_case_type", "calculation_results", ["case_id", "calculation_type"], unique=False)

    op.create_table(
        "scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_periods", sa.JSON(), nullable=False),
        sa.Column("legal_basis_refs", sa.JSON(), nullable=False),
        sa.Column("calculation_refs", sa.JSON(), nullable=False),
        sa.Column("amount_estimated", sa.Numeric(14, 2), nullable=True),
        sa.Column("weeks_estimated", sa.Numeric(10, 2), nullable=True),
        sa.Column("retroactive_estimated", sa.Numeric(14, 2), nullable=True),
        sa.Column("difference_vs_recognized", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["full_analysis_id"], ["full_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_scenarios_analysis", "scenarios", ["full_analysis_id"], unique=False)
    op.create_index("idx_scenarios_case_type", "scenarios", ["case_id", "scenario_type"], unique=False)

    op.create_table(
        "confidence_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("scope_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("recommended_action", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["full_analysis_id"], ["full_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_confidence_scores_analysis", "confidence_scores", ["full_analysis_id"], unique=False)
    op.create_index("idx_confidence_scores_case_scope", "confidence_scores", ["case_id", "scope"], unique=False)

    op.create_table(
        "analysis_inconsistencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inconsistency_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("legal_rule_refs", sa.JSON(), nullable=False),
        sa.Column("calculation_refs", sa.JSON(), nullable=False),
        sa.Column("economic_impact_estimated", sa.Numeric(14, 2), nullable=True),
        sa.Column("legal_impact", sa.Text(), nullable=True),
        sa.Column("missing_documents", sa.JSON(), nullable=True),
        sa.Column("recommended_action", sa.String(length=80), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["full_analysis_id"], ["full_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_analysis_inconsistencies_analysis", "analysis_inconsistencies", ["full_analysis_id"], unique=False)
    op.create_index("idx_analysis_inconsistencies_case_type", "analysis_inconsistencies", ["case_id", "inconsistency_type"], unique=False)
    op.create_index("idx_analysis_inconsistencies_severity", "analysis_inconsistencies", ["severity"], unique=False)

    op.create_table(
        "full_analysis_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Numeric(5, 2), nullable=False),
        sa.Column("current_step", sa.String(length=180), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["full_analysis_id"], ["full_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_full_analysis_jobs_analysis", "full_analysis_jobs", ["full_analysis_id"], unique=False)
    op.create_index("idx_full_analysis_jobs_case_status", "full_analysis_jobs", ["case_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_full_analysis_jobs_case_status", table_name="full_analysis_jobs")
    op.drop_index("idx_full_analysis_jobs_analysis", table_name="full_analysis_jobs")
    op.drop_table("full_analysis_jobs")
    op.drop_index("idx_analysis_inconsistencies_severity", table_name="analysis_inconsistencies")
    op.drop_index("idx_analysis_inconsistencies_case_type", table_name="analysis_inconsistencies")
    op.drop_index("idx_analysis_inconsistencies_analysis", table_name="analysis_inconsistencies")
    op.drop_table("analysis_inconsistencies")
    op.drop_index("idx_confidence_scores_case_scope", table_name="confidence_scores")
    op.drop_index("idx_confidence_scores_analysis", table_name="confidence_scores")
    op.drop_table("confidence_scores")
    op.drop_index("idx_scenarios_case_type", table_name="scenarios")
    op.drop_index("idx_scenarios_analysis", table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_index("idx_calculation_results_case_type", table_name="calculation_results")
    op.drop_index("idx_calculation_results_analysis", table_name="calculation_results")
    op.drop_table("calculation_results")
    op.drop_index("idx_legal_rule_results_requires_review", table_name="legal_rule_results")
    op.drop_index("idx_legal_rule_results_case_result", table_name="legal_rule_results")
    op.drop_index("idx_legal_rule_results_case_category", table_name="legal_rule_results")
    op.drop_index("idx_legal_rule_results_analysis", table_name="legal_rule_results")
    op.drop_table("legal_rule_results")
    op.drop_index("idx_full_analysis_user_status", table_name="full_analysis")
    op.drop_index("idx_full_analysis_case_version", table_name="full_analysis")
    op.drop_index("idx_full_analysis_case_status", table_name="full_analysis")
    op.drop_index("idx_full_analysis_case_created", table_name="full_analysis")
    op.drop_table("full_analysis")
