"""add public landing conversion

Revision ID: 20260511_0003
Revises: 20260511_0002
Create Date: 2026-05-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260511_0003"
down_revision: Union[str, Sequence[str], None] = "20260511_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("service_interest", sa.String(length=100), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("utm_source", sa.String(length=255), nullable=True),
        sa.Column("utm_medium", sa.String(length=255), nullable=True),
        sa.Column("utm_campaign", sa.String(length=255), nullable=True),
        sa.Column("utm_content", sa.String(length=255), nullable=True),
        sa.Column("utm_term", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("intent", sa.String(length=100), nullable=True),
        sa.Column("intent_confidence", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("accepted_privacy_notice", sa.Boolean(), nullable=False),
        sa.Column("privacy_notice_version", sa.String(length=50), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_leads_email"), "leads", ["email"], unique=False)

    op.create_table(
        "public_contents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_key", sa.String(length=80), nullable=False),
        sa.Column("section_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("subtitle", sa.String(length=500), nullable=True),
        sa.Column("body", sa.JSON(), nullable=True),
        sa.Column("cta_label", sa.String(length=120), nullable=True),
        sa.Column("cta_url", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_public_contents_page_key"),
        "public_contents",
        ["page_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_public_contents_section_key"),
        "public_contents",
        ["section_key"],
        unique=False,
    )

    op.create_table(
        "faq_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.String(length=500), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_faq_items_category"), "faq_items", ["category"], unique=False)

    op.create_table(
        "public_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_name", sa.String(length=120), nullable=False),
        sa.Column("anonymous_id", sa.String(length=120), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_public_events_anonymous_id"), "public_events", ["anonymous_id"], unique=False)
    op.create_index(op.f("ix_public_events_event_name"), "public_events", ["event_name"], unique=False)
    op.create_index(op.f("ix_public_events_lead_id"), "public_events", ["lead_id"], unique=False)
    op.create_index(op.f("ix_public_events_user_id"), "public_events", ["user_id"], unique=False)

    op.create_table(
        "visitor_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("anonymous_id", sa.String(length=120), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("requires_human_followup", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_visitor_intents_anonymous_id"), "visitor_intents", ["anonymous_id"], unique=False)
    op.create_index(op.f("ix_visitor_intents_lead_id"), "visitor_intents", ["lead_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_visitor_intents_lead_id"), table_name="visitor_intents")
    op.drop_index(op.f("ix_visitor_intents_anonymous_id"), table_name="visitor_intents")
    op.drop_table("visitor_intents")

    op.drop_index(op.f("ix_public_events_user_id"), table_name="public_events")
    op.drop_index(op.f("ix_public_events_lead_id"), table_name="public_events")
    op.drop_index(op.f("ix_public_events_event_name"), table_name="public_events")
    op.drop_index(op.f("ix_public_events_anonymous_id"), table_name="public_events")
    op.drop_table("public_events")

    op.drop_index(op.f("ix_faq_items_category"), table_name="faq_items")
    op.drop_table("faq_items")

    op.drop_index(op.f("ix_public_contents_section_key"), table_name="public_contents")
    op.drop_index(op.f("ix_public_contents_page_key"), table_name="public_contents")
    op.drop_table("public_contents")

    op.drop_index(op.f("ix_leads_email"), table_name="leads")
    op.drop_table("leads")
