"""fix requires_review cases without confirmed payment

Revision ID: 20260519_0021
Revises: 20260519_0020
Create Date: 2026-05-19 16:15:00.000000
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260519_0021"
down_revision: Union[str, Sequence[str], None] = "20260519_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STEP_MAPPING = {
    "draft": ("case_draft", "complete_case"),
    "created": ("case_created", "upload_documents"),
    "ready_for_documents": ("documents_pending", "upload_documents"),
    "documents_pending": ("documents_pending", "upload_documents"),
    "documents_uploaded": ("documents_uploaded", "start_preanalysis"),
    "preanalysis_pending": ("preanalysis_pending", "start_preanalysis"),
    "preanalysis_ready": ("preanalysis_ready", "view_preanalysis"),
    "preview_locked": ("preview_locked", "unlock_full_analysis"),
    "payment_not_started": ("preview_locked", "unlock_full_analysis"),
    "payment_order_created": ("payment_order_created", "start_payment_checkout"),
    "payment_pending": ("payment_pending", "wait_payment_confirmation"),
    "payment_approved": ("payment_approved", "unlock_full_analysis"),
    "payment_rejected": ("payment_rejected", "retry_payment"),
    "payment_failed": ("payment_failed", "retry_payment"),
    "payment_expired": ("payment_expired", "retry_payment"),
    "payment_requires_review": ("payment_requires_review", "contact_support"),
    "full_analysis_unlocked": ("analysis_unlocked", "start_full_analysis"),
    "paid_unlocked": ("analysis_unlocked", "start_full_analysis"),
    "analysis_in_progress": ("analysis_in_progress", "wait_analysis"),
    "completed": ("completed", "view_report"),
    "requires_review": ("requires_review", "request_professional_review"),
    "blocked": ("blocked", "contact_support"),
    "closed": ("closed", "view_history"),
    "archived": ("archived", "view_history"),
    "error": ("error", "contact_support"),
}

_PAYMENT_UNLOCK_PRODUCT_CODES = (
    "FULL_ANALYSIS_UNLOCK",
    "ANALISIS_COMPLETO_HISTORIA_LABORAL",
)

_NON_FALLBACK_STATUSES = {
    "requires_review",
    "payment_approved",
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
}


def upgrade() -> None:
    connection = op.get_bind()
    now = datetime.now(timezone.utc)

    rows = connection.execute(
        sa.text(
            """
            SELECT c.id
            FROM cases c
            WHERE c.status = 'requires_review'
              AND NOT EXISTS (
                  SELECT 1
                  FROM orders o
                  WHERE o.case_id = c.id
                    AND o.product_code IN :product_codes
                    AND o.status = 'paid'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM payments p
                  JOIN orders o ON o.id = p.order_id
                  WHERE p.case_id = c.id
                    AND p.status = 'approved'
                    AND o.product_code IN :product_codes
              )
            """
        ).bindparams(sa.bindparam("product_codes", expanding=True)),
        {"product_codes": _PAYMENT_UNLOCK_PRODUCT_CODES},
    ).fetchall()

    for row in rows:
        case_id = row[0]
        previous_status = connection.execute(
            sa.text(
                """
                SELECT previous_status
                FROM case_status_history
                WHERE case_id = :case_id
                  AND new_status = 'requires_review'
                  AND previous_status IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"case_id": case_id},
        ).scalar()

        fallback_status = (
            previous_status
            if previous_status and previous_status not in _NON_FALLBACK_STATUSES and previous_status in _STEP_MAPPING
            else "preview_locked"
        )
        fallback_step, fallback_action = _STEP_MAPPING[fallback_status]

        connection.execute(
            sa.text(
                """
                UPDATE cases
                SET status = :new_status,
                    status_reason = :reason,
                    current_step = :current_step,
                    next_best_action = :next_best_action,
                    updated_at = :updated_at
                WHERE id = :case_id
                """
            ),
            {
                "new_status": fallback_status,
                "reason": "Saneamiento: requires_review sin pago confirmado.",
                "current_step": fallback_step,
                "next_best_action": fallback_action,
                "updated_at": now,
                "case_id": case_id,
            },
        )

        connection.execute(
            sa.text(
                """
                INSERT INTO case_status_history (
                    id,
                    case_id,
                    previous_status,
                    new_status,
                    reason,
                    changed_by_user_id,
                    changed_by_role,
                    source_module,
                    metadata,
                    created_at
                )
                VALUES (
                    :id,
                    :case_id,
                    'requires_review',
                    :new_status,
                    :reason,
                    NULL,
                    'system',
                    'migration',
                    :metadata,
                    :created_at
                )
                """
            ).bindparams(sa.bindparam("metadata", type_=sa.JSON())),
            {
                "id": str(uuid.uuid4()),
                "case_id": case_id,
                "new_status": fallback_status,
                "reason": "Saneamiento: requires_review sin pago confirmado.",
                "metadata": {"migration": "20260519_0021", "reason": "requires_review_without_payment"},
                "created_at": now,
            },
        )


def downgrade() -> None:
    # No-op: data migration not safely reversible.
    pass
