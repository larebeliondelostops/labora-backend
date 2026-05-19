"""add backoffice admin module

Revision ID: 20260519_0020
Revises: 20260518_0019
Create Date: 2026-05-19 00:00:00.000000
"""

from typing import Sequence, Union
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260519_0020"
down_revision: Union[str, Sequence[str], None] = "20260518_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ADMIN_ROLE_PERMISSIONS = {
    "super_admin": [
        "admin.cases.read",
        "admin.cases.assign",
        "admin.cases.update_status",
        "admin.cases.block",
        "admin.documents.read",
        "admin.documents.review",
        "admin.extraction.read",
        "admin.extraction.correct",
        "admin.analysis.read",
        "admin.analysis.review",
        "admin.calculations.read",
        "admin.calculations.review",
        "admin.reports.read",
        "admin.reports.approve",
        "admin.legal_drafts.read",
        "admin.legal_drafts.approve",
        "admin.notes.create",
        "admin.notes.read_internal",
        "admin.payments.read",
        "admin.payments.override_unlock",
        "admin.audit.read",
        "admin.config.read",
        "admin.config.write",
    ],
    "admin_manager": [
        "admin.cases.read",
        "admin.cases.assign",
        "admin.cases.update_status",
        "admin.cases.block",
        "admin.documents.read",
        "admin.extraction.read",
        "admin.analysis.read",
        "admin.calculations.read",
        "admin.reports.read",
        "admin.legal_drafts.read",
        "admin.notes.create",
        "admin.notes.read_internal",
        "admin.payments.read",
        "admin.audit.read",
        "admin.config.read",
    ],
    "document_reviewer": [
        "admin.cases.read",
        "admin.documents.read",
        "admin.documents.review",
        "admin.extraction.read",
        "admin.notes.create",
        "admin.notes.read_internal",
    ],
    "legal_reviewer": [
        "admin.cases.read",
        "admin.documents.read",
        "admin.extraction.read",
        "admin.analysis.read",
        "admin.analysis.review",
        "admin.calculations.read",
        "admin.reports.read",
        "admin.reports.approve",
        "admin.legal_drafts.read",
        "admin.legal_drafts.approve",
        "admin.notes.create",
        "admin.notes.read_internal",
    ],
    "calculation_reviewer": [
        "admin.cases.read",
        "admin.extraction.read",
        "admin.calculations.read",
        "admin.calculations.review",
        "admin.notes.create",
        "admin.notes.read_internal",
    ],
    "support_agent": [
        "admin.cases.read",
        "admin.documents.read",
        "admin.notes.create",
        "admin.notes.read_internal",
        "admin.payments.read",
    ],
    "read_only_admin": [
        "admin.cases.read",
        "admin.documents.read",
        "admin.extraction.read",
        "admin.analysis.read",
        "admin.calculations.read",
        "admin.reports.read",
        "admin.legal_drafts.read",
        "admin.notes.read_internal",
        "admin.payments.read",
    ],
}


def upgrade() -> None:
    op.create_table(
        "admin_user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("full_name", sa.String(length=180), nullable=False),
        sa.Column("email", sa.String(length=180), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_admin_user_user_id", "admin_user", ["user_id"], unique=False)
    op.create_index("idx_admin_user_role_status", "admin_user", ["role", "status"], unique=False)

    op.create_table(
        "admin_role_permission",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("permission", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role", "permission", name="uq_admin_role_permission"),
    )
    op.create_index("idx_admin_role_permission_role", "admin_role_permission", ["role"], unique=False)

    permission_table = sa.table(
        "admin_role_permission",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("role", sa.String),
        sa.column("permission", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "id": uuid.uuid4(),
                "role": role,
                "permission": permission,
                "created_at": datetime.now(timezone.utc),
            }
            for role, permissions in ADMIN_ROLE_PERMISSIONS.items()
            for permission in permissions
        ],
    )

    op.create_table(
        "case_queue_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_number", sa.String(length=80), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_stage", sa.String(length=80), nullable=False),
        sa.Column("admin_status", sa.String(length=80), nullable=False),
        sa.Column("priority", sa.String(length=30), nullable=False),
        sa.Column("assigned_to_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_role", sa.String(length=80), nullable=True),
        sa.Column("payment_status", sa.String(length=50), nullable=True),
        sa.Column("document_status", sa.String(length=50), nullable=True),
        sa.Column("analysis_status", sa.String(length=50), nullable=True),
        sa.Column("legal_review_status", sa.String(length=50), nullable=True),
        sa.Column("calculation_review_status", sa.String(length=50), nullable=True),
        sa.Column("has_low_confidence_ai", sa.Boolean(), nullable=False),
        sa.Column("has_blocking_issue", sa.Boolean(), nullable=False),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_case_queue_item_case"),
    )
    op.create_index("idx_case_queue_item_status_priority", "case_queue_item", ["admin_status", "priority"], unique=False)
    op.create_index("idx_case_queue_item_assigned_admin", "case_queue_item", ["assigned_to_admin_id"], unique=False)
    op.create_index("idx_case_queue_item_stage", "case_queue_item", ["current_stage"], unique=False)
    op.create_index("idx_case_queue_item_sla_due", "case_queue_item", ["sla_due_at"], unique=False)

    op.create_table(
        "assignment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_to_admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by_admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_by_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["assigned_to_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_assignment_case_status", "assignment", ["case_id", "status"], unique=False)
    op.create_index("idx_assignment_admin_status", "assignment", ["assigned_to_admin_id", "status"], unique=False)

    op.create_table(
        "internal_note",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_type", sa.String(length=60), nullable=False),
        sa.Column("visibility", sa.String(length=40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("related_entity_type", sa.String(length=80), nullable=True),
        sa.Column("related_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_internal_note_case_created", "internal_note", ["case_id", "created_at"], unique=False)
    op.create_index("idx_internal_note_visibility", "internal_note", ["visibility"], unique=False)

    op.create_table(
        "admin_audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_state", sa.JSON(), nullable=True),
        sa.Column("new_state", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_admin_audit_event_case_created", "admin_audit_event", ["case_id", "created_at"], unique=False)
    op.create_index("idx_admin_audit_event_actor_created", "admin_audit_event", ["actor_admin_id", "created_at"], unique=False)
    op.create_index("idx_admin_audit_event_type", "admin_audit_event", ["event_type"], unique=False)
    op.create_index("idx_admin_audit_event_entity", "admin_audit_event", ["entity_type", "entity_id"], unique=False)

    op.create_table(
        "admin_review_task",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column("assigned_to_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_system", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_admin_review_task_case_status", "admin_review_task", ["case_id", "status"], unique=False)
    op.create_index("idx_admin_review_task_assigned_status", "admin_review_task", ["assigned_to_admin_id", "status"], unique=False)
    op.create_index("idx_admin_review_task_due", "admin_review_task", ["due_at"], unique=False)

    op.create_table(
        "admin_review_decision",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_type", sa.String(length=80), nullable=False),
        sa.Column("decision", sa.String(length=60), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_task_id"], ["admin_review_task.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_admin_review_decision_case_created", "admin_review_decision", ["case_id", "created_at"], unique=False)
    op.create_index("idx_admin_review_decision_task", "admin_review_decision", ["review_task_id"], unique=False)

    op.create_table(
        "ai_confidence_alert",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by_admin_id"], ["admin_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_confidence_alert_case_resolved", "ai_confidence_alert", ["case_id", "resolved"], unique=False)
    op.create_index("idx_ai_confidence_alert_source", "ai_confidence_alert", ["source"], unique=False)
    op.create_index("idx_ai_confidence_alert_severity", "ai_confidence_alert", ["severity"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_ai_confidence_alert_severity", table_name="ai_confidence_alert")
    op.drop_index("idx_ai_confidence_alert_source", table_name="ai_confidence_alert")
    op.drop_index("idx_ai_confidence_alert_case_resolved", table_name="ai_confidence_alert")
    op.drop_table("ai_confidence_alert")
    op.drop_index("idx_admin_review_decision_task", table_name="admin_review_decision")
    op.drop_index("idx_admin_review_decision_case_created", table_name="admin_review_decision")
    op.drop_table("admin_review_decision")
    op.drop_index("idx_admin_review_task_due", table_name="admin_review_task")
    op.drop_index("idx_admin_review_task_assigned_status", table_name="admin_review_task")
    op.drop_index("idx_admin_review_task_case_status", table_name="admin_review_task")
    op.drop_table("admin_review_task")
    op.drop_index("idx_admin_audit_event_entity", table_name="admin_audit_event")
    op.drop_index("idx_admin_audit_event_type", table_name="admin_audit_event")
    op.drop_index("idx_admin_audit_event_actor_created", table_name="admin_audit_event")
    op.drop_index("idx_admin_audit_event_case_created", table_name="admin_audit_event")
    op.drop_table("admin_audit_event")
    op.drop_index("idx_internal_note_visibility", table_name="internal_note")
    op.drop_index("idx_internal_note_case_created", table_name="internal_note")
    op.drop_table("internal_note")
    op.drop_index("idx_assignment_admin_status", table_name="assignment")
    op.drop_index("idx_assignment_case_status", table_name="assignment")
    op.drop_table("assignment")
    op.drop_index("idx_case_queue_item_sla_due", table_name="case_queue_item")
    op.drop_index("idx_case_queue_item_stage", table_name="case_queue_item")
    op.drop_index("idx_case_queue_item_assigned_admin", table_name="case_queue_item")
    op.drop_index("idx_case_queue_item_status_priority", table_name="case_queue_item")
    op.drop_table("case_queue_item")
    op.drop_index("idx_admin_role_permission_role", table_name="admin_role_permission")
    op.drop_table("admin_role_permission")
    op.drop_index("idx_admin_user_role_status", table_name="admin_user")
    op.drop_index("idx_admin_user_user_id", table_name="admin_user")
    op.drop_table("admin_user")
