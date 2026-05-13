"""add consent compliance module

Revision ID: 20260513_0005
Revises: 20260511_0004
Create Date: 2026-05-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260513_0005"
down_revision: Union[str, Sequence[str], None] = "20260511_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "legal_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_plain_text", sa.Text(), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("hash_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "type",
            "version",
            name="uq_legal_document_type_version",
        ),
    )
    op.create_index(
        "idx_legal_documents_type_status",
        "legal_documents",
        ["type", "status"],
        unique=False,
    )
    op.create_index(
        "idx_legal_documents_effective",
        "legal_documents",
        ["effective_from", "effective_to"],
        unique=False,
    )

    op.create_table(
        "user_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_type", sa.String(length=80), nullable=False),
        sa.Column("document_version", sa.String(length=50), nullable=False),
        sa.Column("document_hash_sha256", sa.String(length=64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("locale", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("evidence_hash_sha256", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["legal_document_id"], ["legal_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_consents_user_id"),
        "user_consents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "idx_user_consents_user_type",
        "user_consents",
        ["user_id", "consent_type"],
        unique=False,
    )
    op.create_index(
        "idx_user_consents_user_accepted_at",
        "user_consents",
        ["user_id", "accepted_at"],
        unique=False,
    )
    op.create_index(
        "uq_user_consent_document_once",
        "user_consents",
        ["user_id", "legal_document_id"],
        unique=True,
        postgresql_where=sa.text("accepted = true AND revoked_at IS NULL"),
    )

    op.create_table(
        "consent_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("device_fingerprint_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("session_id", sa.String(length=120), nullable=True),
        sa.Column("accepted_text_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("acceptance_payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_consent_id"], ["user_consents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_consent_evidence_user_consent_id"),
        "consent_evidence",
        ["user_consent_id"],
        unique=False,
    )

    op.create_table(
        "consent_idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_hash_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_consent_idempotency_user_key",
        ),
    )
    op.create_index(
        op.f("ix_consent_idempotency_keys_user_id"),
        "consent_idempotency_keys",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_consent_idempotency_keys_user_id"),
        table_name="consent_idempotency_keys",
    )
    op.drop_table("consent_idempotency_keys")

    op.drop_index(
        op.f("ix_consent_evidence_user_consent_id"),
        table_name="consent_evidence",
    )
    op.drop_table("consent_evidence")

    op.drop_index("uq_user_consent_document_once", table_name="user_consents")
    op.drop_index("idx_user_consents_user_accepted_at", table_name="user_consents")
    op.drop_index("idx_user_consents_user_type", table_name="user_consents")
    op.drop_index(op.f("ix_user_consents_user_id"), table_name="user_consents")
    op.drop_table("user_consents")

    op.drop_index("idx_legal_documents_effective", table_name="legal_documents")
    op.drop_index("idx_legal_documents_type_status", table_name="legal_documents")
    op.drop_table("legal_documents")
