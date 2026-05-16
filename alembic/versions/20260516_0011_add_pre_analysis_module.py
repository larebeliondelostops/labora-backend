"""add pre analysis module

Revision ID: 20260516_0011
Revises: 20260515_0010
Create Date: 2026-05-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260516_0011"
down_revision: Union[str, Sequence[str], None] = "20260515_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pre_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("blocked_reason", sa.String(length=64), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("output_schema_version", sa.String(length=32), nullable=True),
        sa.Column("extractor_version", sa.String(length=32), nullable=True),
        sa.Column("ai_provider", sa.String(length=64), nullable=True),
        sa.Column("ai_model", sa.String(length=128), nullable=True),
        sa.Column("preliminary_case_type", sa.String(length=64), nullable=True),
        sa.Column("traffic_light", sa.String(length=16), nullable=True),
        sa.Column("viability_level", sa.String(length=16), nullable=True),
        sa.Column("completion_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("limited_summary", sa.Text(), nullable=True),
        sa.Column("value_detected_title", sa.String(length=180), nullable=True),
        sa.Column("value_detected_summary", sa.Text(), nullable=True),
        sa.Column("cta_type", sa.String(length=64), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pre_analysis_case_created", "pre_analysis", ["case_id", "created_at"], unique=False)
    op.create_index("idx_pre_analysis_case_status", "pre_analysis", ["case_id", "status"], unique=False)
    op.create_index("idx_pre_analysis_user_status", "pre_analysis", ["user_id", "status"], unique=False)
    op.create_index("idx_pre_analysis_input_hash", "pre_analysis", ["case_id", "input_hash"], unique=False)

    op.create_table(
        "pre_issue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pre_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("public_summary", sa.Text(), nullable=False),
        sa.Column("locked_detail_available", sa.Boolean(), nullable=False),
        sa.Column("evidence_label", sa.String(length=180), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pre_analysis_id"], ["pre_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pre_issue_pre_analysis", "pre_issue", ["pre_analysis_id"], unique=False)
    op.create_index("idx_pre_issue_case", "pre_issue", ["case_id"], unique=False)
    op.create_index("idx_pre_issue_type", "pre_issue", ["issue_type"], unique=False)

    op.create_table(
        "pre_viability",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pre_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("traffic_light", sa.String(length=16), nullable=False),
        sa.Column("short_reason", sa.Text(), nullable=False),
        sa.Column("public_recommendation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pre_analysis_id"], ["pre_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pre_viability_pre_analysis", "pre_viability", ["pre_analysis_id"], unique=False)
    op.create_index("idx_pre_viability_case", "pre_viability", ["case_id"], unique=False)

    op.create_table(
        "missing_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pre_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("upload_hint", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pre_analysis_id"], ["pre_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_missing_document_case_status", "missing_document", ["case_id", "status"], unique=False)
    op.create_index("idx_missing_document_pre_analysis", "missing_document", ["pre_analysis_id"], unique=False)

    op.create_table(
        "case_signal",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pre_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("signal_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("public_summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("source_refs", sa.JSON(), nullable=True),
        sa.Column("is_visible_to_user", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pre_analysis_id"], ["pre_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_case_signal_case_visible", "case_signal", ["case_id", "is_visible_to_user"], unique=False)
    op.create_index("idx_case_signal_pre_analysis", "case_signal", ["pre_analysis_id"], unique=False)
    op.create_index("idx_case_signal_type", "case_signal", ["signal_type"], unique=False)

    op.create_table(
        "pre_analysis_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pre_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["pre_analysis_id"], ["pre_analysis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pre_analysis_job_pre_analysis", "pre_analysis_job", ["pre_analysis_id"], unique=False)
    op.create_index("idx_pre_analysis_job_case_status", "pre_analysis_job", ["case_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_pre_analysis_job_case_status", table_name="pre_analysis_job")
    op.drop_index("idx_pre_analysis_job_pre_analysis", table_name="pre_analysis_job")
    op.drop_table("pre_analysis_job")
    op.drop_index("idx_case_signal_type", table_name="case_signal")
    op.drop_index("idx_case_signal_pre_analysis", table_name="case_signal")
    op.drop_index("idx_case_signal_case_visible", table_name="case_signal")
    op.drop_table("case_signal")
    op.drop_index("idx_missing_document_pre_analysis", table_name="missing_document")
    op.drop_index("idx_missing_document_case_status", table_name="missing_document")
    op.drop_table("missing_document")
    op.drop_index("idx_pre_viability_case", table_name="pre_viability")
    op.drop_index("idx_pre_viability_pre_analysis", table_name="pre_viability")
    op.drop_table("pre_viability")
    op.drop_index("idx_pre_issue_type", table_name="pre_issue")
    op.drop_index("idx_pre_issue_case", table_name="pre_issue")
    op.drop_index("idx_pre_issue_pre_analysis", table_name="pre_issue")
    op.drop_table("pre_issue")
    op.drop_index("idx_pre_analysis_input_hash", table_name="pre_analysis")
    op.drop_index("idx_pre_analysis_user_status", table_name="pre_analysis")
    op.drop_index("idx_pre_analysis_case_status", table_name="pre_analysis")
    op.drop_index("idx_pre_analysis_case_created", table_name="pre_analysis")
    op.drop_table("pre_analysis")
