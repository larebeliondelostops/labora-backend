import hashlib
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.audit_event import AuditEvent
from app.models.case import LaboraCase
from app.models.case_result import CaseResult
from app.models.document import Document, FileUpload
from app.models.full_analysis import CalculationResult
from app.models.legal_action import DraftExport, LegalDraft
from app.models.payment import Order
from app.models.professional_review import (
    LawyerComment,
    ProfessionalReview,
    ReviewedFile,
    ReviewerAssignment,
)
from app.models.report import ExportFile, Report
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.professional_review_repository import (
    FINAL_REVIEW_STATUSES,
    ProfessionalReviewRepository,
)
from app.repositories.report_repository import ReportRepository
from app.services.case_state_machine import has_confirmed_payment
from app.utils.dates import utc_now


CLIENT_ROLES = {"user", "client"}
ADMIN_ROLES = {"admin", "legal_admin"}
LAWYER_ROLES = {"lawyer", "legal_reviewer"}
SUPPORT_ROLES = {"support", "support_agent", "operator", "legal_ops", "reviewer"}
INTERNAL_ROLES = {*ADMIN_ROLES, *LAWYER_ROLES, *SUPPORT_ROLES, "system"}

REVIEW_PRODUCT_CODE = "PROFESSIONAL_REVIEW"
REVIEW_PRODUCT_NAME = "Revision profesional opcional"
DEFAULT_REVIEW_PRICE_COP = 99000

VALID_REVIEW_STATUSES = {
    "not_started",
    "payment_pending",
    "requested",
    "queued",
    "assigned",
    "in_review",
    "changes_requested",
    "client_action_required",
    "ready_for_approval",
    "approved",
    "completed",
    "rejected",
    "cancelled",
    "blocked",
    "error",
}
ATTENTION_STATUSES = {"blocked", "error", "client_action_required"}
NON_FINAL_ANY_TARGETS = {"blocked", "cancelled", "error"}
VALID_TRANSITIONS = {
    "not_started": {"payment_pending", "requested"},
    "payment_pending": {"requested", "cancelled", "blocked", "error"},
    "requested": {"queued", "assigned", "cancelled", "blocked", "error"},
    "queued": {"assigned", "cancelled", "blocked", "error"},
    "assigned": {"in_review", "queued", "cancelled", "blocked", "error"},
    "in_review": {
        "changes_requested",
        "client_action_required",
        "ready_for_approval",
        "rejected",
        "cancelled",
        "blocked",
        "error",
    },
    "changes_requested": {"in_review", "client_action_required", "rejected", "cancelled", "blocked", "error"},
    "client_action_required": {"in_review", "cancelled", "blocked", "error"},
    "ready_for_approval": {"approved", "in_review", "rejected", "cancelled", "blocked", "error"},
    "approved": {"completed", "blocked", "error"},
    "blocked": {"queued", "assigned", "in_review", "cancelled", "error"},
    "error": {"queued", "blocked", "cancelled"},
}
ALLOWED_REVIEW_TYPES = {
    "report_review",
    "legal_draft_review",
    "lawsuit_draft_review",
    "claim_review",
    "petition_review",
    "calculation_review",
    "full_case_review",
}
ALLOWED_TARGET_TYPES = {"report", "legal_draft", "generated_file", "case_result", "calculation"}
ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
CLIENT_VISIBLE_COMMENT_VISIBILITIES = {"client_visible"}
INTERNAL_COMMENT_VISIBILITIES = {"internal", "lawyer_only", "admin_only"}


class ProfessionalReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.documents = DocumentRepository(db)
        self.payments = PaymentRepository(db)
        self.reports = ReportRepository(db)
        self.repository = ProfessionalReviewRepository(db)
        self.audit_events = AuditEventRepository(db)

    def create_review(
        self,
        case_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        case = self._get_case_or_404(case_id)
        self._require_can_request_review(case, user)
        if not has_confirmed_payment(self.db, case):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_REQUIRED",
                message="Debes confirmar el pago del expediente antes de solicitar revision profesional.",
                details={"caseId": str(case.id), "currentStatus": case.status},
            )
        target_id = self._parse_uuid_or_error(payload.target_id, code="TARGET_NOT_FOUND", message="Documento objetivo no existe.")
        target = self._target_or_404(case=case, target_type=payload.target_type, target_id=target_id)
        existing = self.repository.active_review_for_target(
            case_id=case.id,
            target_type=payload.target_type,
            target_id=target_id,
        )
        if existing is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REVIEW_ALREADY_ACTIVE",
                message="Ya existe una revision profesional activa para este objetivo.",
                details={"reviewId": str(existing.id), "status": existing.status},
            )

        now = utc_now()
        requires_payment = bool(payload.requires_payment)
        review = self.repository.create_review(
            case_id=case.id,
            client_id=case.owner_user_id,
            requested_by=user.id,
            status="payment_pending" if requires_payment else "requested",
            review_type=payload.review_type,
            target_type=payload.target_type,
            target_id=target_id,
            priority=payload.priority,
            requires_payment=requires_payment,
            summary_for_reviewer=_summary_hint(case, target),
            client_notes=_sanitize_text(payload.client_notes),
            ai_confidence=_target_confidence(target),
            created_at=now,
            updated_at=now,
        )
        if requires_payment:
            order = self._create_payment_order(case=case, review=review, amount_cop=payload.amount_cop)
            review.payment_order_id = order.id
            self.repository.create_review_order(
                professional_review_id=review.id,
                payment_order_id=order.id,
                amount_cop=order.total_amount,
                currency=order.currency,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            self._audit(
                "revision_profesional.payment_pending",
                actor=user,
                review=review,
                previous_status="not_started",
                new_status=review.status,
                metadata={"paymentOrderId": str(order.id), "amountCop": order.total_amount},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self._audit(
            "revision_profesional.created",
            actor=user,
            review=review,
            previous_status="not_started",
            new_status=review.status,
            metadata={
                "reviewType": review.review_type,
                "targetType": review.target_type,
                "targetId": str(review.target_id),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "id": str(review.id),
            "caseId": str(case.id),
            "status": review.status,
            "requiresPayment": review.requires_payment,
            "paymentOrderId": str(review.payment_order_id) if review.payment_order_id else None,
            "nextAction": "pay_review_order" if review.requires_payment else "wait_assignment",
        }, status.HTTP_201_CREATED

    def list_reviews(
        self,
        *,
        status_filter: str | None,
        case_id: str | None,
        mine: bool,
        page: int,
        page_size: int,
        user: User,
    ) -> dict[str, Any]:
        if status_filter and status_filter not in VALID_REVIEW_STATUSES:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="INVALID_REVIEW_STATUS",
                message="Estado de revision invalido.",
                details={"status": status_filter},
            )
        parsed_case_id = _parse_uuid(case_id)
        if case_id and parsed_case_id is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        if not (self._is_admin(user) or self._is_support(user) or self._is_client(user) or self._is_lawyer(user)):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_CASE_ACCESS",
                message="Usuario sin permiso para listar revisiones.",
            )
        include_all = self._is_admin(user) or self._is_support(user)
        client_id = user.id if self._is_client(user) and not include_all else None
        lawyer_id = user.id if self._is_lawyer(user) and not include_all else None
        reviews, total = self.repository.list_reviews(
            status=status_filter,
            case_id=parsed_case_id,
            client_id=client_id,
            lawyer_id=lawyer_id,
            include_all=include_all,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._list_item(review) for review in reviews],
            "pagination": {"page": page, "pageSize": page_size, "total": total},
        }

    def get_review(
        self,
        review_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_can_view_review(review, user)
        self._audit(
            "revision_profesional.viewed",
            actor=user,
            review=review,
            metadata={"surface": "detail"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def update_review(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_can_update_review(review, user)
        previous_status = review.status
        status_changed = False
        if payload.status is not None and payload.status != review.status:
            self._require_status_transition(review, payload.status, actor=user)
            self._apply_status(review, payload.status, actor=user, reason=payload.blocked_reason)
            status_changed = True
        if payload.priority is not None:
            self._require_admin(user)
            review.priority = payload.priority
        if payload.internal_notes is not None:
            self._require_internal(user)
            review.internal_notes = _sanitize_text(payload.internal_notes)
        if payload.due_at is not None:
            self._require_internal(user)
            review.due_at = payload.due_at
        if payload.blocked_reason is not None:
            self._require_internal(user)
            review.blocked_reason = _sanitize_text(payload.blocked_reason)
        if payload.risk_level is not None:
            self._require_internal(user)
            review.risk_level = payload.risk_level
        review.updated_at = utc_now()
        self._audit(
            "revision_profesional.updated",
            actor=user,
            review=review,
            previous_status=previous_status if status_changed else None,
            new_status=review.status if status_changed else None,
            metadata={"priority": review.priority, "riskLevel": review.risk_level},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def assign_lawyer(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin(user)
        review = self._get_review_or_404(review_id)
        if review.status == "payment_pending":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_NOT_CONFIRMED",
                message="No se puede asignar abogado antes de confirmar el pago.",
            )
        lawyer = self._get_user_or_404(payload.lawyer_id, code="LAWYER_NOT_AVAILABLE", message="Abogado no disponible.")
        if not self._is_lawyer(lawyer) or not lawyer.is_active or lawyer.status != "active":
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="LAWYER_NOT_AVAILABLE",
                message="El usuario seleccionado no es un abogado activo.",
            )
        current = self.repository.get_assignment(review.reviewer_assignment_id)
        if current is not None and current.assignment_status in {"assigned", "accepted"}:
            current.assignment_status = "reassigned"
            current.updated_at = utc_now()
        previous_status = review.status
        now = utc_now()
        assignment = self.repository.create_assignment(
            professional_review_id=review.id,
            lawyer_id=lawyer.id,
            assigned_by=user.id,
            assignment_status="assigned",
            assigned_at=now,
            assignment_notes=_sanitize_text(payload.assignment_notes),
            created_at=now,
            updated_at=now,
        )
        review.reviewer_assignment_id = assignment.id
        review.due_at = payload.due_at or review.due_at
        self._apply_status(review, "assigned", actor=user)
        self._audit(
            "revision_profesional.assigned",
            actor=user,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={"assignmentId": str(assignment.id), "lawyerId": str(lawyer.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"assignmentId": str(assignment.id), "status": assignment.assignment_status, "lawyerId": str(lawyer.id)}

    def assignment_response(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        assignment = self._current_assignment_or_404(review)
        if assignment.lawyer_id != user.id and not self._is_admin(user):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_CASE_ACCESS",
                message="Solo el abogado asignado puede responder la asignacion.",
            )
        previous_status = review.status
        now = utc_now()
        if payload.response == "accepted":
            assignment.assignment_status = "accepted"
            assignment.accepted_at = now
            assignment.updated_at = now
            self._apply_status(review, "in_review", actor=user)
            event = "revision_profesional.assignment_accepted"
        else:
            assignment.assignment_status = "rejected"
            assignment.rejected_at = now
            assignment.rejection_reason = _sanitize_text(payload.reason)
            assignment.updated_at = now
            review.reviewer_assignment_id = None
            self._apply_status(review, "queued", actor=user)
            event = "revision_profesional.assignment_rejected"
        self._audit(
            event,
            actor=user,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={"assignmentId": str(assignment.id), "reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def create_comment(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_can_comment(review, user, visibility=payload.visibility)
        now = utc_now()
        comment = self.repository.create_comment(
            professional_review_id=review.id,
            author_id=user.id,
            visibility=payload.visibility,
            comment_type=payload.comment_type,
            body=_sanitize_text(payload.body) or "",
            target_section=_sanitize_text(payload.target_section),
            target_file_id=_parse_uuid(payload.target_file_id),
            target_version_id=_parse_uuid(payload.target_version_id),
            resolved=False,
            created_at=now,
            updated_at=now,
        )
        self._audit(
            "revision_profesional.comment.created",
            actor=user,
            review=review,
            metadata={"commentId": str(comment.id), "visibility": comment.visibility, "commentType": comment.comment_type},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._comment_payload(comment)

    def resolve_comment(
        self,
        review_id: str,
        comment_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        comment = self._get_comment_or_404(comment_id)
        if comment.professional_review_id != review.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="COMMENT_NOT_FOUND",
                message="Comentario no encontrado.",
            )
        if not (comment.author_id == user.id or self._is_assigned_lawyer(review, user) or self._is_admin(user)):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_CASE_ACCESS",
                message="No tienes permisos para resolver este comentario.",
            )
        comment.resolved = payload.resolved
        comment.resolved_by = user.id if payload.resolved else None
        comment.resolved_at = utc_now() if payload.resolved else None
        comment.updated_at = utc_now()
        self._audit(
            "revision_profesional.comment.resolved",
            actor=user,
            review=review,
            metadata={"commentId": str(comment.id), "resolved": comment.resolved},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._comment_payload(comment)

    def request_client_action(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_assigned_lawyer_or_admin(review, user)
        previous_status = review.status
        self._apply_status(review, "client_action_required", actor=user)
        comment = self.repository.create_comment(
            professional_review_id=review.id,
            author_id=user.id,
            visibility="client_visible",
            comment_type="missing_document" if payload.reason == "missing_document" else "correction_request",
            body=_sanitize_text(payload.message) or "",
            resolved=False,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self._audit(
            "revision_profesional.client_action_requested",
            actor=user,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={
                "reason": payload.reason,
                "commentId": str(comment.id),
                "requiredDocuments": _json_safe(payload.required_documents),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def add_reviewed_file(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_assigned_lawyer_or_admin(review, user)
        file_meta = self._resolve_file_metadata(review, payload)
        version_number = payload.version_number or self.repository.next_file_version(review.id)
        now = utc_now()
        reviewed_file = self.repository.create_file(
            professional_review_id=review.id,
            source_file_id=_parse_uuid(payload.source_file_id),
            generated_file_id=_parse_uuid(payload.generated_file_id),
            uploaded_file_id=_parse_uuid(payload.uploaded_file_id),
            file_type=payload.file_type,
            version_number=version_number,
            status=payload.status,
            checksum=payload.checksum or file_meta["checksum"],
            storage_path=payload.storage_path or file_meta["storage_path"],
            mime_type=payload.mime_type or file_meta["mime_type"],
            file_size=payload.file_size if payload.file_size is not None else file_meta["file_size"],
            created_by=user.id,
            created_at=now,
            updated_at=now,
        )
        previous_status = review.status
        if reviewed_file.status == "ready_for_approval" and review.status in {"in_review", "changes_requested"}:
            self._apply_status(review, "ready_for_approval", actor=user)
        self._audit(
            "revision_profesional.file_reviewed",
            actor=user,
            review=review,
            previous_status=previous_status if review.status != previous_status else None,
            new_status=review.status if review.status != previous_status else None,
            metadata={"reviewedFileId": str(reviewed_file.id), "versionNumber": reviewed_file.version_number},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._file_payload(reviewed_file)

    def approve_review(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_assigned_lawyer_or_admin(review, user)
        reviewed_file = self.repository.get_file(payload.reviewed_file_id)
        if reviewed_file is not None and reviewed_file.professional_review_id != review.id:
            reviewed_file = None
        if reviewed_file is None and not payload.approve_original:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="REVIEW_FILE_REQUIRED",
                message="Falta archivo revisado o aprobacion explicita del documento original.",
            )
        previous_status = review.status
        self._apply_status(review, "approved", actor=user)
        if reviewed_file is not None:
            reviewed_file.status = "published" if payload.publish_to_client else "approved"
            reviewed_file.approved_by = user.id
            reviewed_file.approved_at = utc_now()
            reviewed_file.updated_at = utc_now()
            if payload.publish_to_client:
                self.repository.mark_other_files_archived(review_id=review.id, keep_file_id=reviewed_file.id)
        if payload.approval_note:
            self.repository.create_comment(
                professional_review_id=review.id,
                author_id=user.id,
                visibility="client_visible",
                comment_type="approval_note",
                body=_sanitize_text(payload.approval_note) or "",
                resolved=True,
                resolved_by=user.id,
                resolved_at=utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        self._publish_target(review, reviewed_file=reviewed_file, actor=user)
        self._audit(
            "revision_profesional.approved",
            actor=user,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={"reviewedFileId": str(reviewed_file.id) if reviewed_file else None, "publishToClient": payload.publish_to_client},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if payload.publish_to_client:
            approved_status = review.status
            self._apply_status(review, "completed", actor=user)
            self._audit(
                "revision_profesional.completed",
                actor=user,
                review=review,
                previous_status=approved_status,
                new_status=review.status,
                metadata={"reviewedFileId": str(reviewed_file.id) if reviewed_file else None},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def reject_review(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_assigned_lawyer_or_admin(review, user)
        previous_status = review.status
        self._apply_status(review, "rejected", actor=user)
        review.blocked_reason = _sanitize_text(payload.reason)
        if payload.client_visible_message:
            self.repository.create_comment(
                professional_review_id=review.id,
                author_id=user.id,
                visibility="client_visible",
                comment_type="legal_observation",
                body=_sanitize_text(payload.client_visible_message) or "",
                resolved=False,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        self._audit(
            "revision_profesional.rejected",
            actor=user,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={"reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def cancel_review(
        self,
        review_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        if not (self._is_admin(user) or review.client_id == user.id):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="FORBIDDEN_CASE_ACCESS",
                message="No tienes permisos para cancelar esta revision.",
            )
        if review.client_id == user.id and review.status not in {"payment_pending", "requested", "queued", "assigned"}:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="INVALID_STATUS_TRANSITION",
                message="El cliente solo puede cancelar si la revision no ha iniciado.",
                details={"currentStatus": review.status, "requestedStatus": "cancelled"},
            )
        previous_status = review.status
        self._apply_status(review, "cancelled", actor=user)
        review.cancellation_reason = _sanitize_text(payload.reason)
        if review.review_order and review.review_order.status == "pending":
            review.review_order.status = "cancelled"
            review.review_order.updated_at = utc_now()
        self._audit(
            "revision_profesional.cancelled",
            actor=user,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={"reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(review, user=user)

    def ai_summary(
        self,
        review_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        review = self._get_review_or_404(review_id)
        self._require_assigned_lawyer_or_admin(review, user)
        case = self._get_case_or_404(review.case_id)
        target = self._target_or_404(case=case, target_type=review.target_type, target_id=review.target_id)
        summary = _summary_hint(case, target) or f"Revision profesional del expediente {case.case_number}."
        alerts = []
        if review.risk_level in {"high", "critical"}:
            alerts.append({"type": "risk_level", "severity": review.risk_level, "message": "El expediente tiene nivel de riesgo elevado."})
        if review.target_type == "calculation":
            alerts.append({"type": "calculation_review", "severity": "medium", "message": "Validar insumos y formula antes de aprobar."})
        if not alerts:
            alerts.append({"type": "human_review_required", "severity": "low", "message": "Salida generada como apoyo; requiere criterio profesional."})
        confidence = float(review.ai_confidence or Decimal("0.82"))
        metadata = {
            "modelProvider": "mock",
            "modelName": "labora-professional-review-support",
            "promptVersion": "professional-review-summary-v1",
            "inputRefs": [str(review.case_id), str(review.target_id)],
            "output": {"summary": summary, "alerts": alerts},
            "confidence": confidence,
            "createdAt": utc_now().isoformat(),
        }
        review.summary_for_reviewer = summary
        review.ai_confidence = Decimal(str(confidence))
        review.ai_summary_metadata = metadata
        review.updated_at = utc_now()
        self._audit(
            "revision_profesional.ai_summary.generated",
            actor=user,
            review=review,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "summary": summary,
            "alerts": alerts,
            "confidence": confidence,
            "disclaimer": "Salida generada por IA para apoyo del abogado. No sustituye revision profesional.",
        }

    def confirm_payment_order(self, payment_order_id: str | uuid.UUID, *, commit: bool = True) -> None:
        parsed = _parse_uuid(payment_order_id)
        if parsed is None:
            return
        review = (
            self.db.query(ProfessionalReview)
            .filter(ProfessionalReview.payment_order_id == parsed)
            .order_by(desc(ProfessionalReview.created_at))
            .first()
        )
        if review is None or review.status != "payment_pending":
            return
        previous_status = review.status
        self._apply_status(review, "requested", actor=None)
        if review.review_order:
            review.review_order.status = "paid"
            review.review_order.updated_at = utc_now()
        self._audit(
            "revision_profesional.payment_confirmed",
            actor=None,
            review=review,
            previous_status=previous_status,
            new_status=review.status,
            metadata={"paymentOrderId": str(parsed)},
            ip_address=None,
            user_agent=None,
        )
        if commit:
            self.db.commit()

    def _list_item(self, review: ProfessionalReview) -> dict[str, Any]:
        case = self._get_case_or_404(review.case_id)
        client = self.db.get(User, review.client_id)
        assignment = self.repository.get_assignment(review.reviewer_assignment_id)
        lawyer = self.db.get(User, assignment.lawyer_id) if assignment else None
        return {
            "id": str(review.id),
            "caseId": str(review.case_id),
            "caseNumber": case.case_number,
            "clientName": _user_name(client),
            "reviewType": review.review_type,
            "targetType": review.target_type,
            "status": review.status,
            "priority": review.priority,
            "assignedLawyerName": _user_name(lawyer),
            "dueAt": review.due_at,
            "createdAt": review.created_at,
            "updatedAt": review.updated_at,
        }

    def _detail_payload(self, review: ProfessionalReview, *, user: User) -> dict[str, Any]:
        case = self._get_case_or_404(review.case_id)
        target = self._target_or_404(case=case, target_type=review.target_type, target_id=review.target_id)
        assignment = self.repository.get_assignment(review.reviewer_assignment_id)
        comments = [
            self._comment_payload(comment)
            for comment in self.repository.list_comments(review.id)
            if self._comment_visible_to(comment, user)
        ]
        internal_allowed = self._can_view_internal(review, user)
        return {
            "id": str(review.id),
            "caseId": str(review.case_id),
            "caseNumber": case.case_number,
            "clientId": str(review.client_id),
            "requestedBy": str(review.requested_by),
            "status": review.status,
            "reviewType": review.review_type,
            "targetType": review.target_type,
            "targetId": str(review.target_id),
            "priority": review.priority,
            "requiresPayment": review.requires_payment,
            "paymentOrderId": str(review.payment_order_id) if review.payment_order_id else None,
            "summaryForReviewer": review.summary_for_reviewer if internal_allowed else None,
            "clientNotes": review.client_notes,
            "internalNotes": review.internal_notes if internal_allowed else None,
            "dueAt": review.due_at,
            "startedAt": review.started_at,
            "approvedAt": review.approved_at,
            "completedAt": review.completed_at,
            "cancelledAt": review.cancelled_at,
            "cancellationReason": review.cancellation_reason,
            "blockedReason": review.blocked_reason if internal_allowed else None,
            "riskLevel": review.risk_level if internal_allowed else None,
            "aiConfidence": float(review.ai_confidence) if review.ai_confidence is not None and internal_allowed else None,
            "case": self._case_payload(case),
            "target": self._target_payload(target),
            "assignment": self._assignment_payload(assignment) if assignment and internal_allowed else None,
            "comments": comments,
            "reviewedFiles": [self._file_payload(item, include_storage_path=False) for item in self.repository.list_files(review.id)],
            "auditEvents": [self._audit_payload(item) for item in self._list_audit_events(review) if internal_allowed],
            "nextActions": self._next_actions(review, user),
            "createdAt": review.created_at,
            "updatedAt": review.updated_at,
        }

    def _case_payload(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "status": case.status,
            "holderName": f"{case.holder_first_name} {case.holder_last_name}".strip(),
            "caseTypeRequested": case.case_type_requested,
            "situationType": case.situation_type,
        }

    def _target_payload(self, target: Any) -> dict[str, Any]:
        if isinstance(target, Report):
            return {"id": str(target.id), "type": "report", "title": target.title, "status": target.status}
        if isinstance(target, LegalDraft):
            return {"id": str(target.id), "type": "legal_draft", "title": target.title, "status": target.status}
        if isinstance(target, ExportFile):
            return {"id": str(target.id), "type": "generated_file", "fileName": target.file_name, "status": target.status}
        if isinstance(target, DraftExport):
            return {"id": str(target.id), "type": "generated_file", "fileName": target.file_name, "status": target.status}
        if isinstance(target, CaseResult):
            return {"id": str(target.id), "type": "case_result", "status": target.status}
        if isinstance(target, CalculationResult):
            return {
                "id": str(target.id),
                "type": "calculation",
                "calculationCode": target.calculation_code,
                "calculationName": target.calculation_name,
            }
        return {"id": str(getattr(target, "id", ""))}

    def _assignment_payload(self, assignment: ReviewerAssignment | None) -> dict[str, Any] | None:
        if assignment is None:
            return None
        lawyer = self.db.get(User, assignment.lawyer_id)
        return {
            "id": str(assignment.id),
            "lawyerId": str(assignment.lawyer_id),
            "lawyerName": _user_name(lawyer),
            "assignmentStatus": assignment.assignment_status,
            "assignedBy": str(assignment.assigned_by) if assignment.assigned_by else None,
            "assignedAt": assignment.assigned_at,
            "acceptedAt": assignment.accepted_at,
            "rejectedAt": assignment.rejected_at,
            "rejectionReason": assignment.rejection_reason,
            "assignmentNotes": assignment.assignment_notes,
        }

    def _comment_payload(self, comment: LawyerComment) -> dict[str, Any]:
        return {
            "id": str(comment.id),
            "authorId": str(comment.author_id),
            "visibility": comment.visibility,
            "commentType": comment.comment_type,
            "body": comment.body,
            "targetSection": comment.target_section,
            "targetFileId": str(comment.target_file_id) if comment.target_file_id else None,
            "targetVersionId": str(comment.target_version_id) if comment.target_version_id else None,
            "resolved": comment.resolved,
            "resolvedBy": str(comment.resolved_by) if comment.resolved_by else None,
            "resolvedAt": comment.resolved_at,
            "createdAt": comment.created_at,
            "updatedAt": comment.updated_at,
        }

    def _file_payload(self, file: ReviewedFile, *, include_storage_path: bool = False) -> dict[str, Any]:
        payload = {
            "id": str(file.id),
            "sourceFileId": str(file.source_file_id) if file.source_file_id else None,
            "generatedFileId": str(file.generated_file_id) if file.generated_file_id else None,
            "uploadedFileId": str(file.uploaded_file_id) if file.uploaded_file_id else None,
            "fileType": file.file_type,
            "versionNumber": file.version_number,
            "status": file.status,
            "checksum": file.checksum,
            "mimeType": file.mime_type,
            "fileSize": file.file_size,
            "approvedBy": str(file.approved_by) if file.approved_by else None,
            "approvedAt": file.approved_at,
            "createdAt": file.created_at,
            "updatedAt": file.updated_at,
        }
        if include_storage_path:
            payload["storagePath"] = file.storage_path
        return payload

    def _audit_payload(self, event: AuditEvent) -> dict[str, Any]:
        previous_status = (event.previous_state or {}).get("status") if event.previous_state else None
        new_status = (event.new_state or {}).get("status") if event.new_state else None
        return {
            "id": str(event.id),
            "eventName": event.event_type,
            "previousStatus": previous_status,
            "newStatus": new_status,
            "metadata": event.metadata_json or {},
            "createdAt": event.created_at,
        }

    def _target_or_404(self, *, case: LaboraCase, target_type: str, target_id: uuid.UUID) -> Any:
        if target_type not in ALLOWED_TARGET_TYPES:
            raise ApiError(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, code="TARGET_NOT_FOUND", message="Documento objetivo no existe.")
        target = None
        if target_type == "report":
            target = self.db.get(Report, target_id)
            if target is None or target.case_id != case.id or target.deleted_at is not None:
                target = None
            elif target.status not in {"ready", "approved", "requires_review"}:
                raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code="TARGET_NOT_FOUND", message="El informe aun no esta generado.")
        elif target_type == "legal_draft":
            target = self.db.get(LegalDraft, target_id)
            if target is None or target.case_id != case.id:
                target = None
            elif target.status not in {"ready_for_edit", "quality_check_passed", "requires_review", "approved", "export_ready", "exported"}:
                raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code="TARGET_NOT_FOUND", message="El borrador aun no esta generado.")
        elif target_type == "generated_file":
            target = self.db.get(ExportFile, target_id)
            if target is not None and target.case_id == case.id:
                return target
            target = self.db.get(DraftExport, target_id)
            if target is None or target.case_id != case.id:
                target = None
        elif target_type == "case_result":
            target = self.db.get(CaseResult, target_id)
            if target is None or target.case_id != case.id:
                target = None
        elif target_type == "calculation":
            target = self.db.get(CalculationResult, target_id)
            if target is None or target.case_id != case.id:
                target = None
        if target is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="TARGET_NOT_FOUND",
                message="Documento objetivo no existe.",
            )
        return target

    def _resolve_file_metadata(self, review: ProfessionalReview, payload) -> dict[str, Any]:
        refs = [payload.source_file_id, payload.generated_file_id, payload.uploaded_file_id]
        if not any(refs) and not payload.storage_path:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="REVIEW_FILE_REQUIRED",
                message="Debes asociar un archivo existente o una ruta de storage.",
            )
        document_id = _parse_uuid(payload.source_file_id)
        if document_id is not None:
            document = self.db.get(Document, document_id)
            if document is None or document.case_id != review.case_id or document.deleted_at is not None:
                raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code="TARGET_NOT_FOUND", message="Archivo asociado no existe.")
            return {
                "storage_path": document.storage_key,
                "mime_type": document.mime_type,
                "file_size": document.size_bytes,
                "checksum": document.sha256_hash,
            }
        upload_id = _parse_uuid(payload.uploaded_file_id)
        if upload_id is not None:
            uploaded_document = self.db.get(Document, upload_id)
            if uploaded_document is not None and uploaded_document.case_id == review.case_id and uploaded_document.deleted_at is None:
                return {
                    "storage_path": uploaded_document.storage_key,
                    "mime_type": uploaded_document.mime_type,
                    "file_size": uploaded_document.size_bytes,
                    "checksum": uploaded_document.sha256_hash,
                }
            upload = self.db.get(FileUpload, upload_id)
            if upload is None or upload.case_id != review.case_id:
                raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code="TARGET_NOT_FOUND", message="Archivo asociado no existe.")
            return {
                "storage_path": upload.storage_key,
                "mime_type": upload.mime_type,
                "file_size": upload.size_bytes,
                "checksum": _sha256_text(upload.storage_key),
            }
        generated_id = _parse_uuid(payload.generated_file_id)
        if generated_id is not None:
            export = self.db.get(ExportFile, generated_id)
            if export is not None and export.case_id == review.case_id:
                return {
                    "storage_path": export.storage_key or f"generated/report/{export.id}",
                    "mime_type": export.mime_type,
                    "file_size": export.file_size_bytes or 0,
                    "checksum": export.checksum_sha256,
                }
            draft_export = self.db.get(DraftExport, generated_id)
            if draft_export is not None and draft_export.case_id == review.case_id:
                return {
                    "storage_path": draft_export.storage_key or f"generated/draft/{draft_export.id}",
                    "mime_type": draft_export.mime_type,
                    "file_size": draft_export.file_size_bytes or 0,
                    "checksum": draft_export.checksum_sha256,
                }
            raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code="TARGET_NOT_FOUND", message="Archivo generado no existe.")
        storage_path = payload.storage_path or f"professional-reviews/{review.id}/{uuid.uuid4()}"
        return {
            "storage_path": storage_path,
            "mime_type": payload.mime_type or "application/octet-stream",
            "file_size": payload.file_size or 0,
            "checksum": payload.checksum or _sha256_text(storage_path),
        }

    def _publish_target(self, review: ProfessionalReview, *, reviewed_file: ReviewedFile | None, actor: User) -> None:
        target = self._target_or_404(case=self._get_case_or_404(review.case_id), target_type=review.target_type, target_id=review.target_id)
        now = utc_now()
        if isinstance(target, Report):
            target.status = "approved"
            target.requires_human_review = False
            target.approved_by = actor.id
            target.approved_at = now
            target.updated_at = now
        elif isinstance(target, LegalDraft):
            target.status = "approved"
            target.is_locked = False
            target.last_edited_by = actor.id
            target.updated_at = now
        elif isinstance(target, (ExportFile, DraftExport)) and reviewed_file is not None:
            target.status = "ready"
            target.updated_at = now

    def _create_payment_order(self, *, case: LaboraCase, review: ProfessionalReview, amount_cop: int | None) -> Order:
        now = utc_now()
        amount = int(amount_cop or DEFAULT_REVIEW_PRICE_COP)
        return self.payments.create_order(
            case_id=case.id,
            user_id=case.owner_user_id,
            paywall_id=None,
            status="created",
            currency="COP",
            subtotal_amount=amount,
            tax_amount=0,
            discount_amount=0,
            total_amount=amount,
            product_code=REVIEW_PRODUCT_CODE,
            product_name=REVIEW_PRODUCT_NAME,
            description=f"Revision profesional del expediente {case.case_number}.",
            expires_at=now + timedelta(days=1),
            metadata_json={"reviewId": str(review.id), "productCode": REVIEW_PRODUCT_CODE},
            created_at=now,
            updated_at=now,
        )

    def _apply_status(self, review: ProfessionalReview, new_status: str, *, actor: User | None, reason: str | None = None) -> None:
        if new_status not in VALID_REVIEW_STATUSES:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="INVALID_REVIEW_STATUS",
                message="Estado de revision invalido.",
                details={"requestedStatus": new_status},
            )
        now = utc_now()
        review.status = new_status
        review.updated_at = now
        if new_status == "in_review" and review.started_at is None:
            review.started_at = now
        elif new_status == "approved":
            review.approved_at = now
        elif new_status == "completed":
            review.completed_at = now
        elif new_status == "cancelled":
            review.cancelled_at = now
        elif new_status == "blocked" and reason:
            review.blocked_reason = _sanitize_text(reason)

    def _require_status_transition(self, review: ProfessionalReview, new_status: str, *, actor: User) -> None:
        if review.status in FINAL_REVIEW_STATUSES:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="INVALID_STATUS_TRANSITION",
                message="No se puede modificar una revision finalizada.",
                details={"currentStatus": review.status, "requestedStatus": new_status},
            )
        allowed = VALID_TRANSITIONS.get(review.status, set())
        if new_status in allowed or (new_status in NON_FINAL_ANY_TARGETS and review.status not in FINAL_REVIEW_STATUSES):
            return
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="INVALID_STATUS_TRANSITION",
            message=f"No se puede pasar de {review.status} a {new_status}.",
            details={"currentStatus": review.status, "requestedStatus": new_status},
        )

    def _next_actions(self, review: ProfessionalReview, user: User) -> list[str]:
        actions: list[str] = []
        if review.status == "payment_pending" and review.client_id == user.id:
            actions.append("pay_review_order")
        if self._is_admin(user) and review.status in {"requested", "queued", "assigned"}:
            actions.append("assign_lawyer")
        if self._is_assigned_lawyer(review, user) and review.status == "assigned":
            actions.append("respond_assignment")
        if self._is_assigned_lawyer(review, user) and review.status in {"in_review", "changes_requested"}:
            actions.extend(["comment", "request_client_action", "upload_reviewed_file", "approve"])
        if review.client_id == user.id and review.status == "client_action_required":
            actions.append("upload_requested_documents")
        if self._is_admin(user) and review.status in ATTENTION_STATUSES:
            actions.append("resolve_attention")
        return actions

    def _list_audit_events(self, review: ProfessionalReview) -> list[AuditEvent]:
        return (
            self.db.query(AuditEvent)
            .filter(AuditEvent.entity_type == "professional_review", AuditEvent.entity_id == review.id)
            .order_by(AuditEvent.created_at)
            .all()
        )

    def _audit(
        self,
        event_name: str,
        *,
        actor: User | None,
        review: ProfessionalReview,
        previous_status: str | None = None,
        new_status: str | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        meta = {
            "caseId": str(review.case_id),
            "reviewId": str(review.id),
            "actorId": str(actor.id) if actor else None,
            "actorRole": self._actor_role(actor) if actor else "system",
            "previousStatus": previous_status,
            "newStatus": new_status,
        }
        if metadata:
            meta.update(_json_safe(metadata))
        self.audit_events.create(
            event_type=event_name,
            entity_type="professional_review",
            entity_id=review.id,
            actor_user_id=actor.id if actor else None,
            previous_state={"status": previous_status} if previous_status else None,
            new_state={"status": new_status} if new_status else None,
            metadata=meta,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_review_or_404(self, review_id: str | uuid.UUID) -> ProfessionalReview:
        review = self.repository.get(review_id)
        if review is None or review.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REVIEW_NOT_FOUND",
                message="Revision profesional no encontrada.",
            )
        return review

    def _get_comment_or_404(self, comment_id: str | uuid.UUID) -> LawyerComment:
        comment = self.repository.get_comment(comment_id)
        if comment is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="COMMENT_NOT_FOUND",
                message="Comentario no encontrado.",
            )
        return comment

    def _get_user_or_404(self, user_id: str | uuid.UUID, *, code: str, message: str) -> User:
        parsed = _parse_uuid(user_id)
        user = self.db.get(User, parsed) if parsed else None
        if user is None:
            raise ApiError(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, code=code, message=message)
        return user

    def _current_assignment_or_404(self, review: ProfessionalReview) -> ReviewerAssignment:
        assignment = self.repository.get_assignment(review.reviewer_assignment_id)
        if assignment is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ASSIGNMENT_NOT_FOUND",
                message="No hay abogado asignado a esta revision.",
            )
        return assignment

    def _parse_uuid_or_error(self, value: Any, *, code: str, message: str) -> uuid.UUID:
        parsed = _parse_uuid(value)
        if parsed is None:
            raise ApiError(status_code=status.HTTP_404_NOT_FOUND, code=code, message=message)
        return parsed

    def _require_can_request_review(self, case: LaboraCase, user: User) -> None:
        if self._is_admin(user) or case.owner_user_id == user.id:
            return
        owner = self.cases.get_owner(case_id=case.id, user_id=user.id, roles={"owner", "creator", "authorized_user"})
        if owner is not None and owner.permissions.get("edit_case") is True:
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="Usuario sin permiso sobre el expediente.",
        )

    def _require_can_view_review(self, review: ProfessionalReview, user: User) -> None:
        if self._is_admin(user) or self._is_support(user) or review.client_id == user.id or self._is_assigned_lawyer(review, user):
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="Usuario sin permiso sobre la revision.",
        )

    def _require_can_update_review(self, review: ProfessionalReview, user: User) -> None:
        if self._is_admin(user) or self._is_assigned_lawyer(review, user):
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="Usuario sin permiso para modificar la revision.",
        )

    def _require_assigned_lawyer_or_admin(self, review: ProfessionalReview, user: User) -> None:
        if self._is_admin(user) or self._is_assigned_lawyer(review, user):
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="Solo el abogado asignado o admin puede ejecutar esta accion.",
        )

    def _require_can_comment(self, review: ProfessionalReview, user: User, *, visibility: str) -> None:
        self._require_can_view_review(review, user)
        if review.client_id == user.id and visibility not in CLIENT_VISIBLE_COMMENT_VISIBILITIES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="COMMENT_VISIBILITY_FORBIDDEN",
                message="El cliente solo puede crear comentarios visibles.",
            )
        if self._is_support(user) and visibility in INTERNAL_COMMENT_VISIBILITIES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="COMMENT_VISIBILITY_FORBIDDEN",
                message="Soporte no puede crear comentarios internos.",
            )

    def _comment_visible_to(self, comment: LawyerComment, user: User) -> bool:
        if self._is_admin(user):
            return True
        if comment.visibility == "client_visible" and (self._is_support(user) or comment.author_id == user.id):
            return True
        review = self.repository.get(comment.professional_review_id)
        if review is None:
            return False
        if review.client_id == user.id:
            return comment.visibility == "client_visible"
        if self._is_assigned_lawyer(review, user):
            return comment.visibility in {"client_visible", "internal", "lawyer_only"}
        return False

    def _can_view_internal(self, review: ProfessionalReview, user: User) -> bool:
        return self._is_admin(user) or self._is_assigned_lawyer(review, user) or self._is_support(user)

    def _is_assigned_lawyer(self, review: ProfessionalReview, user: User) -> bool:
        if not self._is_lawyer(user):
            return False
        assignment = self.repository.get_assignment(review.reviewer_assignment_id)
        return assignment is not None and assignment.lawyer_id == user.id and assignment.assignment_status in {"assigned", "accepted"}

    def _require_admin(self, user: User) -> None:
        if self._is_admin(user):
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="Se requieren permisos de administrador.",
        )

    def _require_internal(self, user: User) -> None:
        if user.role in INTERNAL_ROLES:
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="Se requieren permisos internos.",
        )

    def _is_admin(self, user: User) -> bool:
        return user.role in ADMIN_ROLES

    def _is_lawyer(self, user: User) -> bool:
        return user.role in LAWYER_ROLES

    def _is_support(self, user: User) -> bool:
        return user.role in SUPPORT_ROLES

    def _is_client(self, user: User) -> bool:
        return user.role in CLIENT_ROLES

    def _actor_role(self, user: User | None) -> str:
        if user is None:
            return "system"
        if self._is_admin(user):
            return "admin"
        if self._is_lawyer(user):
            return "lawyer"
        if self._is_support(user):
            return "support"
        return "client"


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", "", value, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\son[a-z]+\s*=\s*(['\"]).*?\1", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"javascript:", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _summary_hint(case: LaboraCase, target: Any) -> str:
    target_label = "documento generado"
    if isinstance(target, Report):
        target_label = f"informe {target.title}"
    elif isinstance(target, LegalDraft):
        target_label = f"borrador {target.title}"
    elif isinstance(target, (ExportFile, DraftExport)):
        target_label = f"archivo {target.file_name}"
    elif isinstance(target, CaseResult):
        target_label = "resultado preliminar"
    elif isinstance(target, CalculationResult):
        target_label = f"calculo {target.calculation_name}"
    holder_name = f"{case.holder_first_name} {case.holder_last_name}".strip()
    return f"Revision profesional solicitada para {target_label} del expediente {case.case_number}, titular {holder_name}."


def _target_confidence(target: Any) -> Decimal | None:
    if isinstance(target, Report):
        return target.ai_confidence
    if isinstance(target, LegalDraft):
        return target.quality_score
    if isinstance(target, CalculationResult):
        return target.confidence
    return None


def _user_name(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or " ".join(part for part in [user.first_name, user.last_name] if part) or user.email


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
