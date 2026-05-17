"""add paywall preview module

Revision ID: 20260517_0012
Revises: 20260516_0011
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260517_0012"
down_revision: Union[str, Sequence[str], None] = "20260516_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "preview_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_title", sa.String(length=180), nullable=False),
        sa.Column("summary_limited", sa.Text(), nullable=False),
        sa.Column("alert_level", sa.String(length=16), nullable=True),
        sa.Column("completion_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("hidden_value_hint", sa.Text(), nullable=True),
        sa.Column("main_finding_teaser", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("ai_model", sa.String(length=128), nullable=True),
        sa.Column("ai_prompt_version", sa.String(length=32), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("output_hash", sa.String(length=128), nullable=True),
        sa.Column("source_preanalysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_preanalysis_id"], ["pre_analysis.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_preview_results_case_created", "preview_results", ["case_id", "created_at"], unique=False)
    op.create_index("idx_preview_results_case_status", "preview_results", ["case_id", "status"], unique=False)
    op.create_index("idx_preview_results_user_status", "preview_results", ["user_id", "status"], unique=False)
    op.create_index(op.f("ix_preview_results_case_id"), "preview_results", ["case_id"], unique=False)
    op.create_index(op.f("ix_preview_results_source_preanalysis_id"), "preview_results", ["source_preanalysis_id"], unique=False)
    op.create_index(op.f("ix_preview_results_user_id"), "preview_results", ["user_id"], unique=False)

    op.create_table(
        "paywalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preview_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("unlock_required", sa.Boolean(), nullable=False),
        sa.Column("unlock_type", sa.String(length=32), nullable=False),
        sa.Column("payment_product_code", sa.String(length=120), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("price_label", sa.String(length=50), nullable=True),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlocked_by_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["preview_result_id"], ["preview_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_paywalls_case_status", "paywalls", ["case_id", "status"], unique=False)
    op.create_index("idx_paywalls_preview", "paywalls", ["preview_result_id"], unique=False)
    op.create_index("idx_paywalls_user_status", "paywalls", ["user_id", "status"], unique=False)
    op.create_index(op.f("ix_paywalls_case_id"), "paywalls", ["case_id"], unique=False)
    op.create_index(op.f("ix_paywalls_user_id"), "paywalls", ["user_id"], unique=False)

    op.create_table(
        "conversion_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_name", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_conversion_events_case_created", "conversion_events", ["case_id", "created_at"], unique=False)
    op.create_index("idx_conversion_events_name", "conversion_events", ["event_name"], unique=False)
    op.create_index("idx_conversion_events_user_created", "conversion_events", ["user_id", "created_at"], unique=False)
    op.create_index(op.f("ix_conversion_events_case_id"), "conversion_events", ["case_id"], unique=False)
    op.create_index(op.f("ix_conversion_events_user_id"), "conversion_events", ["user_id"], unique=False)

    op.create_table(
        "locked_features",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paywall_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_highlighted", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["paywall_id"], ["paywalls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_locked_features_key", "locked_features", ["feature_key"], unique=False)
    op.create_index("idx_locked_features_paywall_order", "locked_features", ["paywall_id", "sort_order"], unique=False)
    op.create_index(op.f("ix_locked_features_paywall_id"), "locked_features", ["paywall_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_locked_features_paywall_id"), table_name="locked_features")
    op.drop_index("idx_locked_features_paywall_order", table_name="locked_features")
    op.drop_index("idx_locked_features_key", table_name="locked_features")
    op.drop_table("locked_features")
    op.drop_index(op.f("ix_conversion_events_user_id"), table_name="conversion_events")
    op.drop_index(op.f("ix_conversion_events_case_id"), table_name="conversion_events")
    op.drop_index("idx_conversion_events_user_created", table_name="conversion_events")
    op.drop_index("idx_conversion_events_name", table_name="conversion_events")
    op.drop_index("idx_conversion_events_case_created", table_name="conversion_events")
    op.drop_table("conversion_events")
    op.drop_index(op.f("ix_paywalls_user_id"), table_name="paywalls")
    op.drop_index(op.f("ix_paywalls_case_id"), table_name="paywalls")
    op.drop_index("idx_paywalls_user_status", table_name="paywalls")
    op.drop_index("idx_paywalls_preview", table_name="paywalls")
    op.drop_index("idx_paywalls_case_status", table_name="paywalls")
    op.drop_table("paywalls")
    op.drop_index(op.f("ix_preview_results_user_id"), table_name="preview_results")
    op.drop_index(op.f("ix_preview_results_source_preanalysis_id"), table_name="preview_results")
    op.drop_index(op.f("ix_preview_results_case_id"), table_name="preview_results")
    op.drop_index("idx_preview_results_user_status", table_name="preview_results")
    op.drop_index("idx_preview_results_case_status", table_name="preview_results")
    op.drop_index("idx_preview_results_case_created", table_name="preview_results")
    op.drop_table("preview_results")
