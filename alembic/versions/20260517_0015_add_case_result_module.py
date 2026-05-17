"""add case result module

Revision ID: 20260517_0015
Revises: 20260517_0014
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260517_0015"
down_revision: Union[str, Sequence[str], None] = "20260517_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("result_type", sa.String(length=60), nullable=False),
        sa.Column("final_viability_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("economic_estimate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommended_route_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("headline", sa.String(length=240), nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=False),
        sa.Column("main_inconsistency", sa.Text(), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("user_explanation", sa.Text(), nullable=True),
        sa.Column("legal_disclaimer", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("is_visible_to_user", sa.Boolean(), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["full_analysis.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_case_result_case_status", "case_result", ["case_id", "status"], unique=False)
    op.create_index("idx_case_result_case_version", "case_result", ["case_id", "version"], unique=False)
    op.create_index("idx_case_result_visible", "case_result", ["case_id", "is_visible_to_user"], unique=False)
    op.create_index(op.f("ix_case_result_analysis_id"), "case_result", ["analysis_id"], unique=False)
    op.create_index(op.f("ix_case_result_case_id"), "case_result", ["case_id"], unique=False)

    op.create_table(
        "final_viability",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("missing_information", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_result_id"], ["case_result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_final_viability_level", "final_viability", ["level"], unique=False)
    op.create_index("idx_final_viability_result", "final_viability", ["case_result_id"], unique=False)
    op.create_index(op.f("ix_final_viability_case_result_id"), "final_viability", ["case_result_id"], unique=False)

    op.create_table(
        "economic_estimate",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("estimated_claimable_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("estimated_retroactive_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("estimated_monthly_difference", sa.Numeric(18, 2), nullable=True),
        sa.Column("recognized_scenario_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("corrected_scenario_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("has_economic_estimate", sa.Boolean(), nullable=False),
        sa.Column("estimate_type", sa.String(length=60), nullable=True),
        sa.Column("calculation_reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("min_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("max_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_result_id"], ["case_result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_economic_estimate_has_estimate", "economic_estimate", ["has_economic_estimate"], unique=False)
    op.create_index("idx_economic_estimate_result", "economic_estimate", ["case_result_id"], unique=False)
    op.create_index(op.f("ix_economic_estimate_case_result_id"), "economic_estimate", ["case_result_id"], unique=False)

    op.create_table(
        "recommended_route",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("route_type", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("next_action_label", sa.String(length=120), nullable=True),
        sa.Column("next_action_type", sa.String(length=60), nullable=True),
        sa.Column("next_action_url", sa.String(length=300), nullable=True),
        sa.Column("requires_documents", sa.Boolean(), nullable=False),
        sa.Column("requires_professional_review", sa.Boolean(), nullable=False),
        sa.Column("can_generate_legal_action", sa.Boolean(), nullable=False),
        sa.Column("recommended_legal_action_type", sa.String(length=60), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("required_documents", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_result_id"], ["case_result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_recommended_route_result", "recommended_route", ["case_result_id"], unique=False)
    op.create_index("idx_recommended_route_type", "recommended_route", ["route_type"], unique=False)
    op.create_index(op.f("ix_recommended_route_case_result_id"), "recommended_route", ["case_result_id"], unique=False)

    op.create_table(
        "result_card",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("card_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("value", sa.String(length=160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(length=60), nullable=True),
        sa.Column("tone", sa.String(length=30), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_result_id"], ["case_result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_result_card_key", "result_card", ["card_key"], unique=False)
    op.create_index("idx_result_card_result_sort", "result_card", ["case_result_id", "sort_order"], unique=False)
    op.create_index(op.f("ix_result_card_case_result_id"), "result_card", ["case_result_id"], unique=False)

    op.create_table(
        "result_inconsistency",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inconsistency_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("legal_impact", sa.String(length=40), nullable=True),
        sa.Column("economic_impact", sa.String(length=40), nullable=True),
        sa.Column("estimated_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("source_document_ids", sa.JSON(), nullable=False),
        sa.Column("source_references", sa.JSON(), nullable=False),
        sa.Column("required_documents", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_result_id"], ["case_result.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_result_inconsistency_result_sort", "result_inconsistency", ["case_result_id", "sort_order"], unique=False)
    op.create_index("idx_result_inconsistency_type", "result_inconsistency", ["inconsistency_type"], unique=False)
    op.create_index(op.f("ix_result_inconsistency_case_result_id"), "result_inconsistency", ["case_result_id"], unique=False)

    op.create_table(
        "result_audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(length=40), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("previous_status", sa.String(length=40), nullable=True),
        sa.Column("new_status", sa.String(length=40), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_result_id"], ["case_result.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_result_audit_case_created", "result_audit_event", ["case_id", "created_at"], unique=False)
    op.create_index("idx_result_audit_event_type", "result_audit_event", ["event_type"], unique=False)
    op.create_index("idx_result_audit_result_created", "result_audit_event", ["case_result_id", "created_at"], unique=False)
    op.create_index(op.f("ix_result_audit_event_case_id"), "result_audit_event", ["case_id"], unique=False)
    op.create_index(op.f("ix_result_audit_event_case_result_id"), "result_audit_event", ["case_result_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_result_audit_event_case_result_id"), table_name="result_audit_event")
    op.drop_index(op.f("ix_result_audit_event_case_id"), table_name="result_audit_event")
    op.drop_index("idx_result_audit_result_created", table_name="result_audit_event")
    op.drop_index("idx_result_audit_event_type", table_name="result_audit_event")
    op.drop_index("idx_result_audit_case_created", table_name="result_audit_event")
    op.drop_table("result_audit_event")

    op.drop_index(op.f("ix_result_inconsistency_case_result_id"), table_name="result_inconsistency")
    op.drop_index("idx_result_inconsistency_type", table_name="result_inconsistency")
    op.drop_index("idx_result_inconsistency_result_sort", table_name="result_inconsistency")
    op.drop_table("result_inconsistency")

    op.drop_index(op.f("ix_result_card_case_result_id"), table_name="result_card")
    op.drop_index("idx_result_card_result_sort", table_name="result_card")
    op.drop_index("idx_result_card_key", table_name="result_card")
    op.drop_table("result_card")

    op.drop_index(op.f("ix_recommended_route_case_result_id"), table_name="recommended_route")
    op.drop_index("idx_recommended_route_type", table_name="recommended_route")
    op.drop_index("idx_recommended_route_result", table_name="recommended_route")
    op.drop_table("recommended_route")

    op.drop_index(op.f("ix_economic_estimate_case_result_id"), table_name="economic_estimate")
    op.drop_index("idx_economic_estimate_result", table_name="economic_estimate")
    op.drop_index("idx_economic_estimate_has_estimate", table_name="economic_estimate")
    op.drop_table("economic_estimate")

    op.drop_index(op.f("ix_final_viability_case_result_id"), table_name="final_viability")
    op.drop_index("idx_final_viability_result", table_name="final_viability")
    op.drop_index("idx_final_viability_level", table_name="final_viability")
    op.drop_table("final_viability")

    op.drop_index(op.f("ix_case_result_case_id"), table_name="case_result")
    op.drop_index(op.f("ix_case_result_analysis_id"), table_name="case_result")
    op.drop_index("idx_case_result_visible", table_name="case_result")
    op.drop_index("idx_case_result_case_version", table_name="case_result")
    op.drop_index("idx_case_result_case_status", table_name="case_result")
    op.drop_table("case_result")
