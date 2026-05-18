"""add delivery module

Revision ID: 20260518_0019
Revises: 20260518_0018
Create Date: 2026-05-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260518_0019"
down_revision: Union[str, Sequence[str], None] = "20260518_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "delivery_package",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("closure_reason", sa.Text(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_next_steps", sa.JSON(), nullable=True),
        sa.Column("ai_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_delivery_package_case_id", "delivery_package", ["case_id"], unique=False)
    op.create_index("idx_delivery_package_owner_user_id", "delivery_package", ["owner_user_id"], unique=False)
    op.create_index("idx_delivery_package_status", "delivery_package", ["status"], unique=False)
    op.create_index(
        "uq_delivery_package_latest_per_case",
        "delivery_package",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("is_latest = true AND deleted_at IS NULL"),
    )

    op.create_table(
        "download_file",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("file_storage_key", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=80), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("is_unlocked", sa.Boolean(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_package_id"], ["delivery_package.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_download_file_package_status", "download_file", ["delivery_package_id", "status"], unique=False)
    op.create_index("idx_download_file_case_id", "download_file", ["case_id"], unique=False)
    op.create_index("idx_download_file_category", "download_file", ["category"], unique=False)
    op.create_index("idx_download_file_unlocked", "download_file", ["is_unlocked", "status"], unique=False)

    op.create_table(
        "share_link",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_name", sa.String(length=180), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("allowed_file_ids", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("max_views", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ip_hash", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["delivery_package_id"], ["delivery_package.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("idx_share_link_package_status", "share_link", ["delivery_package_id", "status"], unique=False)
    op.create_index("idx_share_link_case_id", "share_link", ["case_id"], unique=False)
    op.create_index("idx_share_link_expires_at", "share_link", ["expires_at"], unique=False)

    op.create_table(
        "case_closure",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["delivery_package_id"], ["delivery_package.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_case_closure_case_status", "case_closure", ["case_id", "status"], unique=False)
    op.create_index("idx_case_closure_package", "case_closure", ["delivery_package_id"], unique=False)

    op.create_table(
        "delivery_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("previous_status", sa.String(length=40), nullable=True),
        sa.Column("new_status", sa.String(length=40), nullable=True),
        sa.Column("ip_hash", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_package_id"], ["delivery_package.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_delivery_event_case_created", "delivery_event", ["case_id", "created_at"], unique=False)
    op.create_index("idx_delivery_event_package_created", "delivery_event", ["delivery_package_id", "created_at"], unique=False)
    op.create_index("idx_delivery_event_event_type", "delivery_event", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_delivery_event_event_type", table_name="delivery_event")
    op.drop_index("idx_delivery_event_package_created", table_name="delivery_event")
    op.drop_index("idx_delivery_event_case_created", table_name="delivery_event")
    op.drop_table("delivery_event")
    op.drop_index("idx_case_closure_package", table_name="case_closure")
    op.drop_index("idx_case_closure_case_status", table_name="case_closure")
    op.drop_table("case_closure")
    op.drop_index("idx_share_link_expires_at", table_name="share_link")
    op.drop_index("idx_share_link_case_id", table_name="share_link")
    op.drop_index("idx_share_link_package_status", table_name="share_link")
    op.drop_table("share_link")
    op.drop_index("idx_download_file_unlocked", table_name="download_file")
    op.drop_index("idx_download_file_category", table_name="download_file")
    op.drop_index("idx_download_file_case_id", table_name="download_file")
    op.drop_index("idx_download_file_package_status", table_name="download_file")
    op.drop_table("download_file")
    op.drop_index("uq_delivery_package_latest_per_case", table_name="delivery_package")
    op.drop_index("idx_delivery_package_status", table_name="delivery_package")
    op.drop_index("idx_delivery_package_owner_user_id", table_name="delivery_package")
    op.drop_index("idx_delivery_package_case_id", table_name="delivery_package")
    op.drop_table("delivery_package")
