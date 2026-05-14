"""add document management module

Revision ID: 20260513_0007
Revises: 20260513_0006
Create Date: 2026-05-13 00:00:00.000000
"""

import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260513_0007"
down_revision: Union[str, Sequence[str], None] = "20260513_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOCUMENT_TYPES = [
    ("historia_laboral", "Historia laboral", "principal", True, True, ["application/pdf"], 50, 10),
    ("cedula", "Cedula", "identidad", False, False, ["application/pdf", "image/jpeg", "image/png"], 10, 20),
    ("resolucion_pensional", "Resolucion pensional", "soporte", False, False, ["application/pdf"], 25, 30),
    ("certificacion_laboral", "Certificacion laboral", "soporte", False, False, ["application/pdf", "image/jpeg", "image/png"], 25, 40),
    ("desprendible_nomina", "Desprendible de nomina", "soporte", False, False, ["application/pdf", "image/jpeg", "image/png"], 10, 50),
    ("acto_administrativo", "Acto administrativo", "soporte", False, False, ["application/pdf"], 25, 60),
    ("certificado_docente", "Certificado docente", "soporte", False, False, ["application/pdf", "image/jpeg", "image/png"], 25, 70),
    ("acta_posesion", "Acta de posesion", "soporte", False, False, ["application/pdf", "image/jpeg", "image/png"], 25, 80),
    ("respuesta_fondo", "Respuesta de fondo", "soporte", False, False, ["application/pdf"], 25, 90),
    ("sentencia_previa", "Sentencia previa", "juridico", False, False, ["application/pdf"], 25, 100),
    ("tutela_previa", "Tutela previa", "juridico", False, False, ["application/pdf"], 25, 110),
    ("otro_soporte", "Otro soporte", "otro", False, False, ["application/pdf", "image/jpeg", "image/png"], 25, 999),
]


def upgrade() -> None:
    op.create_table(
        "document_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("is_required_for_basic_flow", sa.Boolean(), nullable=False),
        sa.Column("is_primary_candidate", sa.Boolean(), nullable=False),
        sa.Column("allowed_mime_types", sa.JSON(), nullable=False),
        sa.Column("max_size_mb", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("idx_document_types_category", "document_types", ["category"], unique=False)
    op.create_index("idx_document_types_active_sort", "document_types", ["active", "sort_order"], unique=False)

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("extension", sa.String(length=20), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_bucket", sa.String(length=120), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("validation_status", sa.String(length=30), nullable=False),
        sa.Column("classification_source", sa.String(length=30), nullable=False),
        sa.Column("ai_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False),
        sa.Column("duplicate_of_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replaced_by_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("is_password_protected", sa.Boolean(), nullable=False),
        sa.Column("is_corrupted", sa.Boolean(), nullable=False),
        sa.Column("virus_scan_status", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_type_id"], ["document_types.id"]),
        sa.ForeignKeyConstraint(["duplicate_of_document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["replaced_by_document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_case_id"), "documents", ["case_id"], unique=False)
    op.create_index("idx_documents_case_status", "documents", ["case_id", "status"], unique=False)
    op.create_index("idx_documents_case_created", "documents", ["case_id", "created_at"], unique=False)
    op.create_index("idx_documents_sha256", "documents", ["sha256_hash"], unique=False)
    op.create_index("idx_documents_deleted_at", "documents", ["deleted_at"], unique=False)

    op.create_table(
        "document_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("rotation", sa.Integer(), nullable=True),
        sa.Column("ocr_status", sa.String(length=30), nullable=False),
        sa.Column("text_extracted", sa.Text(), nullable=True),
        sa.Column("text_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("quality_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),
    )
    op.create_index("idx_document_pages_document", "document_pages", ["document_id"], unique=False)

    op.create_table(
        "file_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("upload_method", sa.String(length=30), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_file_uploads_case_status", "file_uploads", ["case_id", "status"], unique=False)
    op.create_index("idx_file_uploads_document", "file_uploads", ["document_id"], unique=False)

    op.create_table(
        "document_hashes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hash_type", sa.String(length=30), nullable=False),
        sa.Column("hash_value", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "hash_type", name="uq_document_hash_type"),
    )
    op.create_index("idx_document_hashes_value", "document_hashes", ["hash_type", "hash_value"], unique=False)

    op.create_table(
        "document_validations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_document_validations_document_created",
        "document_validations",
        ["document_id", "created_at"],
        unique=False,
    )

    document_types_table = sa.table(
        "document_types",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("category", sa.String),
        sa.column("is_required_for_basic_flow", sa.Boolean),
        sa.column("is_primary_candidate", sa.Boolean),
        sa.column("allowed_mime_types", sa.JSON),
        sa.column("max_size_mb", sa.Integer),
        sa.column("sort_order", sa.Integer),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.utcnow()
    op.bulk_insert(
        document_types_table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name": name,
                "description": None,
                "category": category,
                "is_required_for_basic_flow": required,
                "is_primary_candidate": primary,
                "allowed_mime_types": mime_types,
                "max_size_mb": max_size_mb,
                "sort_order": sort_order,
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
            for code, name, category, required, primary, mime_types, max_size_mb, sort_order in DOCUMENT_TYPES
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_document_validations_document_created", table_name="document_validations")
    op.drop_table("document_validations")
    op.drop_index("idx_document_hashes_value", table_name="document_hashes")
    op.drop_table("document_hashes")
    op.drop_index("idx_file_uploads_document", table_name="file_uploads")
    op.drop_index("idx_file_uploads_case_status", table_name="file_uploads")
    op.drop_table("file_uploads")
    op.drop_index("idx_document_pages_document", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_index("idx_documents_deleted_at", table_name="documents")
    op.drop_index("idx_documents_sha256", table_name="documents")
    op.drop_index("idx_documents_case_created", table_name="documents")
    op.drop_index("idx_documents_case_status", table_name="documents")
    op.drop_index(op.f("ix_documents_case_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index("idx_document_types_active_sort", table_name="document_types")
    op.drop_index("idx_document_types_category", table_name="document_types")
    op.drop_table("document_types")
