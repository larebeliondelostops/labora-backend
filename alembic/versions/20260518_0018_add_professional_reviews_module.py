"""add professional reviews module

Revision ID: 20260518_0018
Revises: 20260517_0017
Create Date: 2026-05-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260518_0018"
down_revision: Union[str, Sequence[str], None] = "20260517_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "professional_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("review_type", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("requires_payment", sa.Boolean(), nullable=False),
        sa.Column("payment_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_assignment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary_for_reviewer", sa.Text(), nullable=True),
        sa.Column("client_notes", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("ai_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("ai_summary_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["payment_order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_professional_reviews_case_status", "professional_reviews", ["case_id", "status"], unique=False)
    op.create_index("idx_professional_reviews_client_status", "professional_reviews", ["client_id", "status"], unique=False)
    op.create_index("idx_professional_reviews_target", "professional_reviews", ["target_type", "target_id"], unique=False)
    op.create_index(
        "uq_professional_reviews_active_target",
        "professional_reviews",
        ["case_id", "target_type", "target_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status NOT IN ('completed', 'rejected', 'cancelled')"),
    )

    op.create_table(
        "reviewer_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lawyer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_status", sa.String(length=30), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("workload_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("assignment_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["lawyer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["professional_review_id"], ["professional_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_reviewer_assignments_review", "reviewer_assignments", ["professional_review_id"], unique=False)
    op.create_index("idx_reviewer_assignments_lawyer_status", "reviewer_assignments", ["lawyer_id", "assignment_status"], unique=False)
    op.create_foreign_key(
        "fk_professional_reviews_reviewer_assignment_id",
        "professional_reviews",
        "reviewer_assignments",
        ["reviewer_assignment_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "lawyer_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("comment_type", sa.String(length=30), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_section", sa.String(length=120), nullable=True),
        sa.Column("target_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["professional_review_id"], ["professional_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_lawyer_comments_review_created", "lawyer_comments", ["professional_review_id", "created_at"], unique=False)
    op.create_index("idx_lawyer_comments_visibility", "lawyer_comments", ["visibility"], unique=False)
    op.create_index("idx_lawyer_comments_resolved", "lawyer_comments", ["resolved"], unique=False)

    op.create_table(
        "reviewed_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("file_type", sa.String(length=40), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["professional_review_id"], ["professional_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_reviewed_files_review_status", "reviewed_files", ["professional_review_id", "status"], unique=False)
    op.create_index(
        "uq_reviewed_files_published",
        "reviewed_files",
        ["professional_review_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    op.create_table(
        "review_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("professional_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_cop", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["professional_review_id"], ["professional_reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("professional_review_id"),
    )
    op.create_index("idx_review_orders_review", "review_orders", ["professional_review_id"], unique=False)
    op.create_index("idx_review_orders_payment", "review_orders", ["payment_order_id"], unique=False)
    op.create_index("idx_review_orders_status", "review_orders", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_review_orders_status", table_name="review_orders")
    op.drop_index("idx_review_orders_payment", table_name="review_orders")
    op.drop_index("idx_review_orders_review", table_name="review_orders")
    op.drop_table("review_orders")
    op.drop_index("uq_reviewed_files_published", table_name="reviewed_files")
    op.drop_index("idx_reviewed_files_review_status", table_name="reviewed_files")
    op.drop_table("reviewed_files")
    op.drop_index("idx_lawyer_comments_resolved", table_name="lawyer_comments")
    op.drop_index("idx_lawyer_comments_visibility", table_name="lawyer_comments")
    op.drop_index("idx_lawyer_comments_review_created", table_name="lawyer_comments")
    op.drop_table("lawyer_comments")
    op.drop_constraint("fk_professional_reviews_reviewer_assignment_id", "professional_reviews", type_="foreignkey")
    op.drop_index("idx_reviewer_assignments_lawyer_status", table_name="reviewer_assignments")
    op.drop_index("idx_reviewer_assignments_review", table_name="reviewer_assignments")
    op.drop_table("reviewer_assignments")
    op.drop_index("uq_professional_reviews_active_target", table_name="professional_reviews")
    op.drop_index("idx_professional_reviews_target", table_name="professional_reviews")
    op.drop_index("idx_professional_reviews_client_status", table_name="professional_reviews")
    op.drop_index("idx_professional_reviews_case_status", table_name="professional_reviews")
    op.drop_table("professional_reviews")
