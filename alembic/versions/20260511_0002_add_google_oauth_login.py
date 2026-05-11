"""add google oauth login

Revision ID: 20260511_0002
Revises: 20260511_0001
Create Date: 2026-05-11 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260511_0002"
down_revision: Union[str, Sequence[str], None] = "20260511_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(length=500), nullable=True))

    op.create_table(
        "external_auth_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=False),
        sa.Column("provider_email", sa.String(length=255), nullable=False),
        sa.Column("provider_email_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_external_auth_provider_user",
        ),
    )
    op.create_index(
        op.f("ix_external_auth_accounts_provider"),
        "external_auth_accounts",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_auth_accounts_user_id"),
        "external_auth_accounts",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("redirect_to", sa.String(length=500), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_oauth_states_provider"),
        "oauth_states",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_states_state_hash"),
        "oauth_states",
        ["state_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_states_state_hash"), table_name="oauth_states")
    op.drop_index(op.f("ix_oauth_states_provider"), table_name="oauth_states")
    op.drop_table("oauth_states")

    op.drop_index(
        op.f("ix_external_auth_accounts_user_id"),
        table_name="external_auth_accounts",
    )
    op.drop_index(
        op.f("ix_external_auth_accounts_provider"),
        table_name="external_auth_accounts",
    )
    op.drop_table("external_auth_accounts")

    op.drop_column("users", "avatar_url")
    op.drop_column("users", "full_name")
