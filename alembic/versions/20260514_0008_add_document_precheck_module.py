"""add document precheck module

Revision ID: 20260514_0008
Revises: 20260513_0007
Create Date: 2026-05-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260514_0008"
down_revision: Union[str, Sequence[str], None] = "20260513_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_prechecks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=True),
        sa.Column("traffic_light", sa.String(length=20), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manual_decision", sa.String(length=40), nullable=True),
        sa.Column("manual_traffic_light", sa.String(length=20), nullable=True),
        sa.Column("manual_notes", sa.Text(), nullable=True),
        sa.Column("manually_reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("manually_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["manually_reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_document_prechecks_case", "document_prechecks", ["case_id"], unique=False)
    op.create_index("idx_document_prechecks_document", "document_prechecks", ["document_id"], unique=False)
    op.create_index(
        "idx_document_prechecks_document_latest",
        "document_prechecks",
        ["document_id", "is_latest"],
        unique=False,
    )
    op.create_index("idx_document_prechecks_status", "document_prechecks", ["status"], unique=False)

    op.create_table(
        "ocr_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("precheck_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("engine", sa.String(length=80), nullable=True),
        sa.Column("pages_total", sa.Integer(), nullable=True),
        sa.Column("pages_processed", sa.Integer(), nullable=False),
        sa.Column("text_detected", sa.Boolean(), nullable=False),
        sa.Column("avg_text_density", sa.Numeric(6, 4), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["precheck_id"], ["document_prechecks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ocr_jobs_case", "ocr_jobs", ["case_id"], unique=False)
    op.create_index("idx_ocr_jobs_document", "ocr_jobs", ["document_id"], unique=False)
    op.create_index("idx_ocr_jobs_status", "ocr_jobs", ["status"], unique=False)

    op.create_table(
        "ocr_page_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ocr_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text_preview", sa.Text(), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=True),
        sa.Column("text_density", sa.Numeric(6, 4), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_blurry", sa.Boolean(), nullable=False),
        sa.Column("is_rotated", sa.Boolean(), nullable=False),
        sa.Column("rotation_degrees", sa.Integer(), nullable=True),
        sa.Column("has_table_like_content", sa.Boolean(), nullable=False),
        sa.Column("detected_labels", sa.JSON(), nullable=True),
        sa.Column("issues_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ocr_job_id"], ["ocr_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ocr_job_id", "page_number", name="uq_ocr_page_result_number"),
    )
    op.create_index("idx_ocr_page_results_document", "ocr_page_results", ["document_id"], unique=False)

    op.create_table(
        "document_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_precheck_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.String(length=80), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_precheck_id"], ["document_prechecks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_document_issues_code", "document_issues", ["code"], unique=False)
    op.create_index("idx_document_issues_document", "document_issues", ["document_id"], unique=False)
    op.create_index("idx_document_issues_precheck", "document_issues", ["document_precheck_id"], unique=False)

    op.create_table(
        "ai_confidences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_precheck_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("task", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("raw_response_hash", sa.String(length=64), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_precheck_id"], ["document_prechecks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_confidences_precheck", "ai_confidences", ["document_precheck_id"], unique=False)
    op.create_index("idx_ai_confidences_provider_task", "ai_confidences", ["provider", "task"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_ai_confidences_provider_task", table_name="ai_confidences")
    op.drop_index("idx_ai_confidences_precheck", table_name="ai_confidences")
    op.drop_table("ai_confidences")
    op.drop_index("idx_document_issues_precheck", table_name="document_issues")
    op.drop_index("idx_document_issues_document", table_name="document_issues")
    op.drop_index("idx_document_issues_code", table_name="document_issues")
    op.drop_table("document_issues")
    op.drop_index("idx_ocr_page_results_document", table_name="ocr_page_results")
    op.drop_table("ocr_page_results")
    op.drop_index("idx_ocr_jobs_status", table_name="ocr_jobs")
    op.drop_index("idx_ocr_jobs_document", table_name="ocr_jobs")
    op.drop_index("idx_ocr_jobs_case", table_name="ocr_jobs")
    op.drop_table("ocr_jobs")
    op.drop_index("idx_document_prechecks_status", table_name="document_prechecks")
    op.drop_index("idx_document_prechecks_document_latest", table_name="document_prechecks")
    op.drop_index("idx_document_prechecks_document", table_name="document_prechecks")
    op.drop_index("idx_document_prechecks_case", table_name="document_prechecks")
    op.drop_table("document_prechecks")
