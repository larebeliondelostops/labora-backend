"""add legal actions module

Revision ID: 20260517_0017
Revises: 20260517_0016
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260517_0017"
down_revision: Union[str, Sequence[str], None] = "20260517_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "legal_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("source_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_route_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("eligibility_status", sa.String(length=50), nullable=False),
        sa.Column("eligibility_reason", sa.Text(), nullable=True),
        sa.Column("professional_review_level", sa.String(length=30), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("pending_data", sa.JSON(), nullable=False),
        sa.Column("missing_attachments", sa.JSON(), nullable=False),
        sa.Column("selected_by_user", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_analysis_id"], ["full_analysis.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_report_id"], ["reports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_legal_actions_case_id", "legal_actions", ["case_id"], unique=False)
    op.create_index("idx_legal_actions_status", "legal_actions", ["status"], unique=False)
    op.create_index("idx_legal_actions_type", "legal_actions", ["action_type"], unique=False)
    op.create_index("idx_legal_actions_user_id", "legal_actions", ["user_id"], unique=False)
    op.create_index(
        "uq_legal_actions_case_type_active",
        "legal_actions",
        ["case_id", "action_type"],
        unique=True,
        postgresql_where=sa.text("status != 'cancelled'"),
    )

    op.create_table(
        "legal_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=180), nullable=False),
        sa.Column("jurisdiction", sa.String(length=80), nullable=True),
        sa.Column("legal_domain", sa.String(length=80), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("content_schema", sa.JSON(), nullable=False),
        sa.Column("required_inputs_schema", sa.JSON(), nullable=False),
        sa.Column("required_attachments_schema", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_type", "legal_domain", "template_version", name="uq_legal_templates_action_domain_version"),
    )
    op.create_index("idx_legal_templates_action_active", "legal_templates", ["action_type", "is_active"], unique=False)

    op.create_table(
        "legal_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("generation_mode", sa.String(length=30), nullable=False),
        sa.Column("document_metadata", sa.JSON(), nullable=False),
        sa.Column("user_inputs", sa.JSON(), nullable=False),
        sa.Column("ai_summary", sa.JSON(), nullable=True),
        sa.Column("quality_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("professional_review_level", sa.String(length=30), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_edited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_edited_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["legal_action_id"], ["legal_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["legal_templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_legal_drafts_action_id", "legal_drafts", ["legal_action_id"], unique=False)
    op.create_index("idx_legal_drafts_case_id", "legal_drafts", ["case_id"], unique=False)
    op.create_index("idx_legal_drafts_status", "legal_drafts", ["status"], unique=False)
    op.create_index("idx_legal_drafts_user_id", "legal_drafts", ["user_id"], unique=False)

    op.create_table(
        "draft_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=True),
        sa.Column("content_plain", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source_references", sa.JSON(), nullable=False),
        sa.Column("pending_markers", sa.JSON(), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("generated_by_ai", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "section_key", name="uq_draft_sections_key"),
    )
    op.create_index("idx_draft_sections_draft_order", "draft_sections", ["draft_id", "order_index"], unique=False)
    op.create_index("idx_draft_sections_status", "draft_sections", ["status"], unique=False)

    op.create_table(
        "draft_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(length=40), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "version_number", name="uq_draft_versions_number"),
    )
    op.create_index("idx_draft_versions_draft_created", "draft_versions", ["draft_id", "created_at"], unique=False)

    op.create_table(
        "draft_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_role", sa.String(length=40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["draft_sections.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_draft_comments_draft_status", "draft_comments", ["draft_id", "status"], unique=False)
    op.create_index("idx_draft_comments_section", "draft_comments", ["section_id"], unique=False)

    op.create_table(
        "draft_quality_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("overall_status", sa.String(length=40), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("critical_warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_draft_quality_checks_draft_created", "draft_quality_checks", ["draft_id", "created_at"], unique=False)
    op.create_index("idx_draft_quality_checks_status", "draft_quality_checks", ["overall_status"], unique=False)

    op.create_table(
        "draft_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("format", sa.String(length=12), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("include_watermark", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_draft_exports_case_format", "draft_exports", ["case_id", "format"], unique=False)
    op.create_index("idx_draft_exports_draft_status", "draft_exports", ["draft_id", "status"], unique=False)

    op.create_table(
        "ai_generation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("output_hash", sa.String(length=128), nullable=False),
        sa.Column("structured_output", sa.JSON(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["section_id"], ["draft_sections.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_generation_runs_case_status", "ai_generation_runs", ["case_id", "status"], unique=False)
    op.create_index("idx_ai_generation_runs_draft", "ai_generation_runs", ["draft_id"], unique=False)
    op.create_index("idx_ai_generation_runs_section", "ai_generation_runs", ["section_id"], unique=False)

    op.create_table(
        "legal_action_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("export_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["legal_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["export_id"], ["draft_exports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["draft_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_legal_action_jobs_idempotency_key"),
    )
    op.create_index("idx_legal_action_jobs_case_status", "legal_action_jobs", ["case_id", "status"], unique=False)
    op.create_index("idx_legal_action_jobs_draft", "legal_action_jobs", ["draft_id"], unique=False)
    op.create_index("idx_legal_action_jobs_type_status", "legal_action_jobs", ["job_type", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_legal_action_jobs_type_status", table_name="legal_action_jobs")
    op.drop_index("idx_legal_action_jobs_draft", table_name="legal_action_jobs")
    op.drop_index("idx_legal_action_jobs_case_status", table_name="legal_action_jobs")
    op.drop_table("legal_action_jobs")
    op.drop_index("idx_ai_generation_runs_section", table_name="ai_generation_runs")
    op.drop_index("idx_ai_generation_runs_draft", table_name="ai_generation_runs")
    op.drop_index("idx_ai_generation_runs_case_status", table_name="ai_generation_runs")
    op.drop_table("ai_generation_runs")
    op.drop_index("idx_draft_exports_draft_status", table_name="draft_exports")
    op.drop_index("idx_draft_exports_case_format", table_name="draft_exports")
    op.drop_table("draft_exports")
    op.drop_index("idx_draft_quality_checks_status", table_name="draft_quality_checks")
    op.drop_index("idx_draft_quality_checks_draft_created", table_name="draft_quality_checks")
    op.drop_table("draft_quality_checks")
    op.drop_index("idx_draft_comments_section", table_name="draft_comments")
    op.drop_index("idx_draft_comments_draft_status", table_name="draft_comments")
    op.drop_table("draft_comments")
    op.drop_index("idx_draft_versions_draft_created", table_name="draft_versions")
    op.drop_table("draft_versions")
    op.drop_index("idx_draft_sections_status", table_name="draft_sections")
    op.drop_index("idx_draft_sections_draft_order", table_name="draft_sections")
    op.drop_table("draft_sections")
    op.drop_index("idx_legal_drafts_user_id", table_name="legal_drafts")
    op.drop_index("idx_legal_drafts_status", table_name="legal_drafts")
    op.drop_index("idx_legal_drafts_case_id", table_name="legal_drafts")
    op.drop_index("idx_legal_drafts_action_id", table_name="legal_drafts")
    op.drop_table("legal_drafts")
    op.drop_index("idx_legal_templates_action_active", table_name="legal_templates")
    op.drop_table("legal_templates")
    op.drop_index("uq_legal_actions_case_type_active", table_name="legal_actions")
    op.drop_index("idx_legal_actions_user_id", table_name="legal_actions")
    op.drop_index("idx_legal_actions_type", table_name="legal_actions")
    op.drop_index("idx_legal_actions_status", table_name="legal_actions")
    op.drop_index("idx_legal_actions_case_id", table_name="legal_actions")
    op.drop_table("legal_actions")
