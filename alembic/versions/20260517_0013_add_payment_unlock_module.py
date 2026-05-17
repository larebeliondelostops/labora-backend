"""add payment unlock module

Revision ID: 20260517_0013
Revises: 20260517_0012
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260517_0013"
down_revision: Union[str, Sequence[str], None] = "20260517_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paywall_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal_amount", sa.Integer(), nullable=False),
        sa.Column("tax_amount", sa.Integer(), nullable=False),
        sa.Column("discount_amount", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("product_code", sa.String(length=120), nullable=False),
        sa.Column("product_name", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paywall_id"], ["paywalls.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_orders_case_id", "orders", ["case_id"], unique=False)
    op.create_index("idx_orders_paywall_id", "orders", ["paywall_id"], unique=False)
    op.create_index("idx_orders_status", "orders", ["status"], unique=False)
    op.create_index("idx_orders_user_id", "orders", ["user_id"], unique=False)
    op.create_index(
        "uq_orders_case_active_unlock",
        "orders",
        ["case_id", "product_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('created', 'checkout_started', 'pending', 'paid')"),
    )

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=120), nullable=True),
        sa.Column("provider_checkout_id", sa.String(length=120), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_status", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("payment_method", sa.String(length=40), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("return_url", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_provider_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_payment"),
    )
    op.create_index("idx_payments_case_id", "payments", ["case_id"], unique=False)
    op.create_index("idx_payments_order_id", "payments", ["order_id"], unique=False)
    op.create_index("idx_payments_status", "payments", ["status"], unique=False)
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"], unique=False)

    op.create_table(
        "payment_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_event_id", sa.String(length=160), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=120), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("provider_status", sa.String(length=80), nullable=True),
        sa.Column("normalized_status", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_payment_transactions_provider_event",
        ),
    )
    op.create_index(
        "idx_payment_transactions_order_id",
        "payment_transactions",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "idx_payment_transactions_payment_id",
        "payment_transactions",
        ["payment_id"],
        unique=False,
    )

    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_number", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "payment_id", name="uq_receipts_order_payment"),
        sa.UniqueConstraint("receipt_number", name="uq_receipts_number"),
    )
    op.create_index("idx_receipts_case_id", "receipts", ["case_id"], unique=False)
    op.create_index("idx_receipts_user_id", "receipts", ["user_id"], unique=False)
    op.create_index(op.f("ix_receipts_order_id"), "receipts", ["order_id"], unique=False)
    op.create_index(op.f("ix_receipts_payment_id"), "receipts", ["payment_id"], unique=False)

    op.create_table(
        "unlock_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("unlocked_features", sa.JSON(), nullable=False),
        sa.Column("previous_case_status", sa.String(length=50), nullable=True),
        sa.Column("new_case_status", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "payment_id", name="uq_unlock_events_case_payment"),
    )
    op.create_index("idx_unlock_events_case_id", "unlock_events", ["case_id"], unique=False)
    op.create_index("idx_unlock_events_order_id", "unlock_events", ["order_id"], unique=False)
    op.create_index(op.f("ix_unlock_events_payment_id"), "unlock_events", ["payment_id"], unique=False)
    op.create_index(op.f("ix_unlock_events_user_id"), "unlock_events", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_unlock_events_user_id"), table_name="unlock_events")
    op.drop_index(op.f("ix_unlock_events_payment_id"), table_name="unlock_events")
    op.drop_index("idx_unlock_events_order_id", table_name="unlock_events")
    op.drop_index("idx_unlock_events_case_id", table_name="unlock_events")
    op.drop_table("unlock_events")
    op.drop_index(op.f("ix_receipts_payment_id"), table_name="receipts")
    op.drop_index(op.f("ix_receipts_order_id"), table_name="receipts")
    op.drop_index("idx_receipts_user_id", table_name="receipts")
    op.drop_index("idx_receipts_case_id", table_name="receipts")
    op.drop_table("receipts")
    op.drop_index("idx_payment_transactions_payment_id", table_name="payment_transactions")
    op.drop_index("idx_payment_transactions_order_id", table_name="payment_transactions")
    op.drop_table("payment_transactions")
    op.drop_index(op.f("ix_payments_user_id"), table_name="payments")
    op.drop_index("idx_payments_status", table_name="payments")
    op.drop_index("idx_payments_order_id", table_name="payments")
    op.drop_index("idx_payments_case_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("uq_orders_case_active_unlock", table_name="orders")
    op.drop_index("idx_orders_user_id", table_name="orders")
    op.drop_index("idx_orders_status", table_name="orders")
    op.drop_index("idx_orders_paywall_id", table_name="orders")
    op.drop_index("idx_orders_case_id", table_name="orders")
    op.drop_table("orders")
