"""add digital case module

Revision ID: 20260513_0006
Revises: 20260513_0005
Create Date: 2026-05-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260513_0006"
down_revision: Union[str, Sequence[str], None] = "20260513_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_number", sa.String(length=50), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("holder_type", sa.String(length=30), nullable=False),
        sa.Column("holder_first_name", sa.String(length=120), nullable=False),
        sa.Column("holder_last_name", sa.String(length=120), nullable=False),
        sa.Column("holder_document_type", sa.String(length=30), nullable=False),
        sa.Column("holder_document_number", sa.String(length=80), nullable=False),
        sa.Column("holder_birth_date", sa.Date(), nullable=True),
        sa.Column("holder_email", sa.String(length=255), nullable=True),
        sa.Column("holder_phone", sa.String(length=30), nullable=True),
        sa.Column("acting_as_third_party", sa.Boolean(), nullable=False),
        sa.Column("third_party_relationship", sa.String(length=60), nullable=True),
        sa.Column(
            "third_party_authorization_status",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column("case_type_requested", sa.String(length=80), nullable=False),
        sa.Column("case_type_suggested", sa.String(length=80), nullable=True),
        sa.Column("case_type_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("pension_fund_or_entity", sa.String(length=160), nullable=True),
        sa.Column("situation_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("current_step", sa.String(length=80), nullable=False),
        sa.Column("next_best_action", sa.String(length=80), nullable=False),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_number"),
    )
    op.create_index(op.f("ix_cases_case_number"), "cases", ["case_number"], unique=True)
    op.create_index(op.f("ix_cases_owner_user_id"), "cases", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_cases_status"), "cases", ["status"], unique=False)
    op.create_index("idx_cases_owner_status", "cases", ["owner_user_id", "status"], unique=False)
    op.create_index("idx_cases_case_type", "cases", ["case_type_requested"], unique=False)
    op.create_index("idx_cases_situation_type", "cases", ["situation_type"], unique=False)
    op.create_index("idx_cases_created_at", "cases", ["created_at"], unique=False)
    op.create_index("idx_cases_updated_at", "cases", ["updated_at"], unique=False)

    op.create_table(
        "case_owners",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "user_id", "role", name="uq_case_owner_user_role"),
    )
    op.create_index(op.f("ix_case_owners_case_id"), "case_owners", ["case_id"], unique=False)
    op.create_index(op.f("ix_case_owners_user_id"), "case_owners", ["user_id"], unique=False)
    op.create_index("idx_case_owners_user_role", "case_owners", ["user_id", "role"], unique=False)

    op.create_table(
        "case_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_by_role", sa.String(length=50), nullable=False),
        sa.Column("source_module", sa.String(length=80), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_status_history_case_id"), "case_status_history", ["case_id"], unique=False)
    op.create_index(
        "idx_case_status_history_case_created",
        "case_status_history",
        ["case_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "case_history_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_history_events_case_id"), "case_history_events", ["case_id"], unique=False)
    op.create_index(
        "idx_case_history_events_case_created",
        "case_history_events",
        ["case_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_case_history_events_visibility",
        "case_history_events",
        ["visibility"],
        unique=False,
    )

    op.create_table(
        "case_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "tag", name="uq_case_tag"),
    )
    op.create_index(op.f("ix_case_tags_case_id"), "case_tags", ["case_id"], unique=False)
    op.create_index("idx_case_tags_tag", "case_tags", ["tag"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_case_tags_tag", table_name="case_tags")
    op.drop_index(op.f("ix_case_tags_case_id"), table_name="case_tags")
    op.drop_table("case_tags")

    op.drop_index("idx_case_history_events_visibility", table_name="case_history_events")
    op.drop_index("idx_case_history_events_case_created", table_name="case_history_events")
    op.drop_index(op.f("ix_case_history_events_case_id"), table_name="case_history_events")
    op.drop_table("case_history_events")

    op.drop_index("idx_case_status_history_case_created", table_name="case_status_history")
    op.drop_index(op.f("ix_case_status_history_case_id"), table_name="case_status_history")
    op.drop_table("case_status_history")

    op.drop_index("idx_case_owners_user_role", table_name="case_owners")
    op.drop_index(op.f("ix_case_owners_user_id"), table_name="case_owners")
    op.drop_index(op.f("ix_case_owners_case_id"), table_name="case_owners")
    op.drop_table("case_owners")

    op.drop_index("idx_cases_updated_at", table_name="cases")
    op.drop_index("idx_cases_created_at", table_name="cases")
    op.drop_index("idx_cases_situation_type", table_name="cases")
    op.drop_index("idx_cases_case_type", table_name="cases")
    op.drop_index("idx_cases_owner_status", table_name="cases")
    op.drop_index(op.f("ix_cases_status"), table_name="cases")
    op.drop_index(op.f("ix_cases_owner_user_id"), table_name="cases")
    op.drop_index(op.f("ix_cases_case_number"), table_name="cases")
    op.drop_table("cases")
