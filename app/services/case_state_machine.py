from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import status

from app.core.api_errors import ApiError
from app.models.payment import Order, Payment

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.case import LaboraCase


PAYMENT_UNLOCK_PRODUCT_CODES = {
    "FULL_ANALYSIS_UNLOCK",
    "ANALISIS_COMPLETO_HISTORIA_LABORAL",
}

PAYMENT_CONFIRMED_CASE_STATUSES = {
    "payment_approved",
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
}

CASE_STATUSES = {
    "draft",
    "created",
    "ready_for_documents",
    "documents_pending",
    "documents_uploaded",
    "preanalysis_pending",
    "preanalysis_ready",
    "preview_locked",
    "payment_not_started",
    "payment_order_created",
    "payment_pending",
    "payment_approved",
    "payment_rejected",
    "payment_failed",
    "payment_expired",
    "payment_requires_review",
    "full_analysis_unlocked",
    "paid_unlocked",
    "analysis_in_progress",
    "completed",
    "requires_review",
    "blocked",
    "closed",
    "archived",
    "error",
}

CASE_STATUS_TRANSITIONS = {
    "draft": {"created", "ready_for_documents", "closed", "archived", "error"},
    "created": {
        "ready_for_documents",
        "documents_pending",
        "documents_uploaded",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "ready_for_documents": {
        "documents_pending",
        "documents_uploaded",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "documents_pending": {
        "documents_uploaded",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "documents_uploaded": {
        "preanalysis_pending",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "preanalysis_pending": {
        "preanalysis_ready",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "preanalysis_ready": {
        "preview_locked",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "preview_locked": {
        "payment_order_created",
        "payment_pending",
        "payment_rejected",
        "payment_failed",
        "payment_expired",
        "paid_unlocked",
        "full_analysis_unlocked",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_not_started": {
        "payment_order_created",
        "payment_pending",
        "preview_locked",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_order_created": {
        "payment_pending",
        "payment_approved",
        "payment_expired",
        "payment_rejected",
        "payment_failed",
        "payment_requires_review",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_pending": {
        "payment_approved",
        "payment_rejected",
        "payment_failed",
        "payment_expired",
        "payment_requires_review",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_approved": {
        "full_analysis_unlocked",
        "paid_unlocked",
        "analysis_in_progress",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_rejected": {
        "payment_order_created",
        "payment_pending",
        "payment_approved",
        "payment_failed",
        "payment_expired",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_failed": {
        "payment_order_created",
        "payment_pending",
        "payment_approved",
        "payment_expired",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_expired": {
        "payment_order_created",
        "payment_pending",
        "payment_approved",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "payment_requires_review": {
        "payment_order_created",
        "payment_pending",
        "payment_rejected",
        "payment_failed",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "full_analysis_unlocked": {
        "analysis_in_progress",
        "completed",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "paid_unlocked": {
        "analysis_in_progress",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "analysis_in_progress": {
        "completed",
        "requires_review",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "completed": {"requires_review", "closed", "archived", "error"},
    "requires_review": {
        "created",
        "ready_for_documents",
        "documents_pending",
        "documents_uploaded",
        "preanalysis_pending",
        "preanalysis_ready",
        "preview_locked",
        "payment_not_started",
        "payment_order_created",
        "payment_pending",
        "payment_approved",
        "payment_rejected",
        "payment_failed",
        "payment_expired",
        "payment_requires_review",
        "full_analysis_unlocked",
        "paid_unlocked",
        "analysis_in_progress",
        "completed",
        "blocked",
        "closed",
        "archived",
        "error",
    },
    "blocked": {
        "created",
        "ready_for_documents",
        "documents_pending",
        "documents_uploaded",
        "requires_review",
        "closed",
        "archived",
        "error",
    },
    "closed": {"archived"},
    "archived": set(),
    "error": {"blocked", "requires_review", "closed", "archived"},
}


def step_for_status(status_value: str) -> tuple[str, str]:
    mapping = {
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
        "full_analysis_unlocked": ("analysis", "start_full_analysis"),
        "paid_unlocked": ("analysis", "start_full_analysis"),
        "analysis_in_progress": ("analysis_in_progress", "wait_analysis"),
        "completed": ("completed", "view_report"),
        "requires_review": ("requires_review", "request_professional_review"),
        "blocked": ("blocked", "contact_support"),
        "closed": ("closed", "view_history"),
        "archived": ("archived", "view_history"),
        "error": ("error", "contact_support"),
    }
    return mapping[status_value]


def has_confirmed_payment(db: Session, case: LaboraCase) -> bool:
    if case.status in PAYMENT_CONFIRMED_CASE_STATUSES:
        return True

    paid_order = (
        db.query(Order.id)
        .filter(
            Order.case_id == case.id,
            Order.product_code.in_(PAYMENT_UNLOCK_PRODUCT_CODES),
            Order.status == "paid",
        )
        .first()
    )
    if paid_order is not None:
        return True

    approved_payment = (
        db.query(Payment.id)
        .join(Order, Payment.order_id == Order.id)
        .filter(
            Payment.case_id == case.id,
            Payment.status == "approved",
            Order.product_code.in_(PAYMENT_UNLOCK_PRODUCT_CODES),
        )
        .first()
    )
    return approved_payment is not None


def validate_case_transition(
    db: Session,
    case: LaboraCase,
    *,
    new_status: str,
    validate_transition: bool = True,
) -> None:
    if new_status not in CASE_STATUSES:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="CASE_VALIDATION_ERROR",
            message="Estado de expediente invalido.",
        )

    previous_status = case.status
    if (
        validate_transition
        and new_status != previous_status
        and new_status not in CASE_STATUS_TRANSITIONS.get(previous_status, set())
    ):
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="CASE_STATE_CONFLICT",
            message=f"Transicion invalida de {previous_status} a {new_status}.",
            details={"currentStatus": previous_status, "requestedStatus": new_status},
        )

    if (
        new_status == "requires_review"
        and previous_status != "requires_review"
        and not has_confirmed_payment(db, case)
    ):
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="PAYMENT_REQUIRED",
            message="El expediente requiere pago confirmado antes de pasar a requires_review.",
            details={"currentStatus": previous_status, "requestedStatus": new_status},
        )
