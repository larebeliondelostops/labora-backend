"""add reports module

Revision ID: 20260517_0016
Revises: 20260517_0015
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260517_0016"
down_revision: Union[str, Sequence[str], None] = "20260517_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("visibility", sa.String(length=40), nullable=False),
        sa.Column("source_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_calculation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ai_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.String(length=80), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_analysis_id"], ["full_analysis.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_reports_case_status", "reports", ["case_id", "status"], unique=False)
    op.create_index("idx_reports_case_type", "reports", ["case_id", "report_type"], unique=False)
    op.create_index("idx_reports_current_version", "reports", ["current_version_id"], unique=False)
    op.create_index("idx_reports_owner_status", "reports", ["owner_user_id", "status"], unique=False)

    op.create_table(
        "report_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("source_refs", sa.JSON(), nullable=True),
        sa.Column("ai_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "section_key", name="uq_report_section_key"),
    )
    op.create_index("idx_report_sections_report_order", "report_sections", ["report_id", "order_index"], unique=False)
    op.create_index("idx_report_sections_status", "report_sections", ["status"], unique=False)

    op.create_table(
        "report_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_markdown", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_role", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "version_number", name="uq_report_version_number"),
    )
    op.create_index("idx_report_versions_report_created", "report_versions", ["report_id", "created_at"], unique=False)
    op.create_index("idx_report_versions_report_status", "report_versions", ["report_id", "status"], unique=False)

    op.create_table(
        "export_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_format", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_version_id"], ["report_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_export_files_case_format", "export_files", ["case_id", "file_format"], unique=False)
    op.create_index("idx_export_files_report_status", "export_files", ["report_id", "status"], unique=False)
    op.create_index("idx_export_files_version", "export_files", ["report_version_id"], unique=False)

    op.create_table(
        "report_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("report_type", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("template_markdown", sa.Text(), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_key", "version", name="uq_report_template_key_version"),
    )
    op.create_index("idx_report_templates_key_status", "report_templates", ["template_key", "status"], unique=False)
    op.create_index("idx_report_templates_type_status", "report_templates", ["report_type", "status"], unique=False)

    op.create_table(
        "report_generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_hash", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("output_hash", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_report_generation_jobs_case_status", "report_generation_jobs", ["case_id", "status"], unique=False)
    op.create_index("idx_report_generation_jobs_report", "report_generation_jobs", ["report_id"], unique=False)
    op.create_index("idx_report_generation_jobs_type_status", "report_generation_jobs", ["job_type", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_report_generation_jobs_type_status", table_name="report_generation_jobs")
    op.drop_index("idx_report_generation_jobs_report", table_name="report_generation_jobs")
    op.drop_index("idx_report_generation_jobs_case_status", table_name="report_generation_jobs")
    op.drop_table("report_generation_jobs")
    op.drop_index("idx_report_templates_type_status", table_name="report_templates")
    op.drop_index("idx_report_templates_key_status", table_name="report_templates")
    op.drop_table("report_templates")
    op.drop_index("idx_export_files_version", table_name="export_files")
    op.drop_index("idx_export_files_report_status", table_name="export_files")
    op.drop_index("idx_export_files_case_format", table_name="export_files")
    op.drop_table("export_files")
    op.drop_index("idx_report_versions_report_status", table_name="report_versions")
    op.drop_index("idx_report_versions_report_created", table_name="report_versions")
    op.drop_table("report_versions")
    op.drop_index("idx_report_sections_status", table_name="report_sections")
    op.drop_index("idx_report_sections_report_order", table_name="report_sections")
    op.drop_table("report_sections")
    op.drop_index("idx_reports_owner_status", table_name="reports")
    op.drop_index("idx_reports_current_version", table_name="reports")
    op.drop_index("idx_reports_case_type", table_name="reports")
    op.drop_index("idx_reports_case_status", table_name="reports")
    op.drop_table("reports")
