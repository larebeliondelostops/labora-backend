import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy import and_, case as sql_case, desc, exists as sql_exists, func, or_
from sqlalchemy.orm import Session

from app.core.admin_dependencies import AdminContext, has_admin_permission
from app.core.api_errors import ApiError
from app.models.admin import (
    AdminAuditEvent,
    AdminReviewDecision,
    AdminReviewTask,
    AdminUser,
    AiConfidenceAlert,
    Assignment,
    CaseQueueItem,
    InternalNote,
)
from app.models.case import CaseHistoryEvent, CaseOwner, CaseStatusHistory, LaboraCase
from app.models.consent import UserConsent
from app.models.document import Document, DocumentPage, DocumentValidation
from app.models.extraction import (
    ContributionGap,
    ContributionWeek,
    Employer,
    ExtractionField,
    ExtractionIssue,
    ExtractionRun,
    LaborNovelty,
    LaborPeriod,
    SalaryBase,
    UserCorrection,
)
from app.models.full_analysis import (
    CalculationResult,
    ConfidenceScore,
    FullAnalysis,
    LegalRuleResult,
    Scenario,
)
from app.models.legal_action import LegalAction, LegalDraft, DraftQualityCheck
from app.models.payment import Order, Payment
from app.models.pre_analysis import PreAnalysis, PreIssue
from app.models.questionnaire import CaseQuestionnaireSession, QuestionnaireAnswer
from app.models.report import Report, ReportVersion
from app.models.user import User
from app.schemas.admin import (
    AdminAssignCaseRequest,
    AdminCaseStatusUpdateRequest,
    DocumentReviewRequest,
    ExtractionCorrectionRequest,
    InternalNoteCreateRequest,
    LegalDraftReviewRequest,
    OverrideUnlockRequest,
    ReportApprovalRequest,
    ResolveAiAlertRequest,
    ReviewDecisionRequest,
)
from app.services.case_state_machine import (
    CASE_STATUSES,
    step_for_status,
    validate_case_transition,
)
from app.utils.dates import utc_now


ADMIN_CASE_STATUSES = {
    "draft",
    "consent_pending",
    "documents_pending",
    "pre_analysis_pending",
    "pre_analysis_ready",
    "preview_locked",
    "payment_pending",
    "payment_confirmed",
    "full_analysis_pending",
    "full_analysis_in_progress",
    "full_analysis_ready",
    "internal_review_pending",
    "approved_for_delivery",
    "returned_to_user",
    "requires_human_review",
    "professional_review_requested",
    "delivered",
    "closed",
    "blocked",
    "error",
    "requires_review",
    "not_started",
    "in_progress",
    "completed",
}
REASON_REQUIRED_STATUSES = {"blocked", "requires_review", "returned_to_user"}
PAYMENT_CONFIRMED_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
    "payment_approved",
}
DELIVERY_STATUSES = {"approved_for_delivery", "delivered"}
OPEN_TASK_STATUSES = {"pending", "open", "queued", "in_progress", "requires_review"}
BLOCKING_ALERT_SEVERITIES = {"critical", "high", "blocking"}


class AdminService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def dashboard_summary(
        self,
        *,
        context: AdminContext,
        from_date: datetime | None,
        to_date: datetime | None,
        assigned_to_me: bool,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        cases = self._case_query(from_date=from_date, to_date=to_date).all()
        queue_items = self._queue_items_for_cases([case.id for case in cases])
        if assigned_to_me:
            cases = [
                case
                for case in cases
                if queue_items.get(case.id)
                and queue_items[case.id].assigned_to_admin_id == context.admin_user.id
            ]

        by_stage: dict[str, int] = {}
        open_cases = 0
        requires_review = 0
        blocked = 0
        ready_for_delivery = 0
        low_confidence = 0
        overdue = 0
        due_today = 0
        today = utc_now().date()
        now = utc_now()
        for case in cases:
            item = queue_items.get(case.id)
            status_value = item.admin_status if item else _admin_status_for_case(case)
            stage = item.current_stage if item else _stage_for_case(case)
            by_stage[stage] = by_stage.get(stage, 0) + 1
            if status_value not in {"closed", "delivered"} and case.status not in {"closed", "archived"}:
                open_cases += 1
            if status_value in {"requires_review", "internal_review_pending", "requires_human_review"}:
                requires_review += 1
            if status_value == "blocked" or (item and item.has_blocking_issue):
                blocked += 1
            if status_value in {"approved_for_delivery", "delivered"}:
                ready_for_delivery += 1
            if item and item.has_low_confidence_ai:
                low_confidence += 1
            if item and item.sla_due_at:
                due = _as_utc(item.sla_due_at)
                if due < now:
                    overdue += 1
                elif due.date() == today:
                    due_today += 1

        unresolved_alerts = (
            self.db.query(func.count(AiConfidenceAlert.id))
            .filter(AiConfidenceAlert.resolved.is_(False))
            .scalar()
            or 0
        )
        low_confidence = max(low_confidence, int(unresolved_alerts))
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="dashboard",
            entity_id=None,
            case_id=None,
            metadata={
                "filters": {
                    "from": from_date,
                    "to": to_date,
                    "assignedToMe": assigned_to_me,
                }
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "totals": {
                "openCases": open_cases,
                "requiresReview": requires_review,
                "blocked": blocked,
                "readyForDelivery": ready_for_delivery,
                "lowConfidenceAlerts": low_confidence,
            },
            "byStage": [
                {"stage": stage, "count": count}
                for stage, count in sorted(by_stage.items())
            ],
            "sla": {"overdue": overdue, "dueToday": due_today},
        }

    def list_cases(
        self,
        *,
        context: AdminContext,
        page: int,
        limit: int,
        search: str | None,
        q: str | None,
        status_filter: str | None,
        stage: str | None,
        priority: str | None,
        assigned_to: str | None,
        payment_status: str | None,
        document_status: str | None,
        has_low_confidence_ai: bool | None,
        has_blocking_issue: bool | None,
        sort: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        query = (
            self.db.query(LaboraCase, CaseQueueItem, AdminUser)
            .outerjoin(CaseQueueItem, CaseQueueItem.case_id == LaboraCase.id)
            .outerjoin(AdminUser, AdminUser.id == CaseQueueItem.assigned_to_admin_id)
            .filter(LaboraCase.deleted_at.is_(None))
        )
        search_value = (search or q or "").strip()
        if search_value:
            like_value = f"%{search_value}%"
            query = query.filter(
                or_(
                    LaboraCase.case_number.ilike(like_value),
                    LaboraCase.holder_first_name.ilike(like_value),
                    LaboraCase.holder_last_name.ilike(like_value),
                    LaboraCase.holder_document_number.ilike(like_value),
                    LaboraCase.holder_email.ilike(like_value),
                )
            )
        if status_filter:
            query = query.filter(
                or_(
                    CaseQueueItem.admin_status == status_filter,
                    and_(CaseQueueItem.id.is_(None), LaboraCase.status == status_filter),
                )
            )
        if stage:
            query = query.filter(
                or_(
                    CaseQueueItem.current_stage == stage,
                    and_(CaseQueueItem.id.is_(None), LaboraCase.current_step == stage),
                    and_(CaseQueueItem.id.is_(None), LaboraCase.status == stage),
                )
            )
        if priority:
            if priority == "normal":
                query = query.filter(or_(CaseQueueItem.priority == "normal", CaseQueueItem.id.is_(None)))
            else:
                query = query.filter(CaseQueueItem.priority == priority)
        if assigned_to:
            query = self._filter_assignee(query, assigned_to, context)
        if payment_status:
            query = query.filter(
                or_(
                    CaseQueueItem.payment_status == payment_status,
                    and_(CaseQueueItem.id.is_(None), LaboraCase.status == payment_status),
                )
            )
        if document_status:
            query = query.filter(CaseQueueItem.document_status == document_status)
        if has_low_confidence_ai is not None:
            alert_exists = sql_exists().where(
                and_(
                    AiConfidenceAlert.case_id == LaboraCase.id,
                    AiConfidenceAlert.resolved.is_(False),
                )
            )
            if has_low_confidence_ai:
                query = query.filter(
                    or_(CaseQueueItem.has_low_confidence_ai.is_(True), alert_exists)
                )
            else:
                query = query.filter(
                    and_(
                        or_(CaseQueueItem.id.is_(None), CaseQueueItem.has_low_confidence_ai.is_(False)),
                        ~alert_exists,
                    )
                )
        if has_blocking_issue is not None:
            blocking_alert_exists = sql_exists().where(
                and_(
                    AiConfidenceAlert.case_id == LaboraCase.id,
                    AiConfidenceAlert.resolved.is_(False),
                    AiConfidenceAlert.severity.in_(BLOCKING_ALERT_SEVERITIES),
                )
            )
            blocking_task_exists = sql_exists().where(
                and_(
                    AdminReviewTask.case_id == LaboraCase.id,
                    AdminReviewTask.blocking.is_(True),
                    AdminReviewTask.status.in_(OPEN_TASK_STATUSES),
                    AdminReviewTask.completed_at.is_(None),
                )
            )
            if has_blocking_issue:
                query = query.filter(
                    or_(
                        CaseQueueItem.has_blocking_issue.is_(True),
                        blocking_alert_exists,
                        blocking_task_exists,
                    )
                )
            else:
                query = query.filter(
                    and_(
                        or_(CaseQueueItem.id.is_(None), CaseQueueItem.has_blocking_issue.is_(False)),
                        ~blocking_alert_exists,
                        ~blocking_task_exists,
                    )
                )

        total = query.with_entities(func.count(LaboraCase.id)).scalar() or 0
        query = self._sort_case_query(query, sort)
        rows = query.offset((page - 1) * limit).limit(limit).all()
        data = [
            self._case_queue_payload(case, item, assignee)
            for case, item, assignee in rows
        ]
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="case_queue",
            entity_id=None,
            case_id=None,
            metadata={
                "filters": {
                    "page": page,
                    "limit": limit,
                    "search": search,
                    "q": q,
                    "status": status_filter,
                    "stage": stage,
                    "priority": priority,
                    "assignedTo": assigned_to,
                    "paymentStatus": payment_status,
                    "documentStatus": document_status,
                    "hasLowConfidenceAi": has_low_confidence_ai,
                    "hasBlockingIssue": has_blocking_issue,
                    "sort": sort,
                }
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "data": data,
            "pagination": {
                "page": page,
                "limit": limit,
                "pageSize": limit,
                "total": total,
            },
        }

    def get_case_detail(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        owner = self.db.get(User, case.owner_user_id)
        queue_item = self._queue_item_for_case(case.id)
        notes = self._notes_for_case(case.id)
        alerts = self._alerts_for_case(case.id)
        audit_events = (
            self.db.query(AdminAuditEvent)
            .filter(AdminAuditEvent.case_id == case.id)
            .order_by(desc(AdminAuditEvent.created_at))
            .limit(50)
            .all()
        )
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="case",
            entity_id=case.id,
            case_id=case.id,
            metadata={"surface": "admin_case_detail"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "id": str(case.id),
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "ownerUserId": str(case.owner_user_id),
            "holderName": _holder_full_name(case),
            "holderDocumentNumber": case.holder_document_number,
            "holderEmail": case.holder_email,
            "holderPhone": case.holder_phone,
            "currentStage": queue_item.current_stage if queue_item else _stage_for_case(case),
            "adminStatus": queue_item.admin_status if queue_item else _admin_status_for_case(case),
            "status": case.status,
            "priority": queue_item.priority if queue_item else "normal",
            "case": self._case_payload(case),
            "holder": self._holder_payload(case),
            "owner": self._user_payload(owner),
            "consents": self._consent_summary(case.owner_user_id),
            "payment": self._payment_summary(case.id),
            "documents": self._documents_payload(case.id),
            "extraction": self._extraction_payload(case.id, include_items=True),
            "questionnaire": self._questionnaire_payload(case.id),
            "preAnalysis": self._pre_analysis_payload(case.id),
            "fullAnalysis": self._full_analysis_payload(case.id),
            "legalRules": self._legal_rules_payload(case.id),
            "calculations": self._calculations_payload(case.id),
            "reports": self._reports_payload(case.id),
            "legalDrafts": self._legal_drafts_payload(case.id),
            "internalNotes": [self._note_payload(note) for note in notes],
            "aiAlerts": [self._alert_payload(alert) for alert in alerts],
            "reviewTasks": [self._review_task_payload(task) for task in self._review_tasks_for_case(case.id)],
            "adminHistory": [self._admin_audit_payload(event) for event in audit_events],
            "history": [self._case_history_payload(event) for event in self._case_history_for_case(case.id)],
        }

    def assign_case(
        self,
        case_id: str,
        payload: AdminAssignCaseRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        assignee = self._resolve_admin_assignee(payload.assigned_to_admin_id or "")
        now = utc_now()
        assignment = Assignment(
            case_id=case.id,
            assigned_to_admin_id=assignee.id,
            assigned_by_admin_id=context.admin_user.id,
            assignment_type=payload.assignment_type or "support_follow_up",
            status="active",
            reason=payload.reason,
            created_at=now,
        )
        self.db.add(assignment)
        queue_item = self._ensure_queue_item(case)
        previous_state = self._queue_state(queue_item)
        queue_item.assigned_to_admin_id = assignee.id
        queue_item.assigned_role = payload.assignment_type
        queue_item.admin_status = "requires_review"
        queue_item.last_activity_at = now
        queue_item.updated_at = now
        if assignee.user_id is not None:
            self._create_or_update_case_owner(
                case_id=case.id,
                user_id=assignee.user_id,
                role=self._case_owner_role(payload.assignment_type or ""),
            )
        self._record_case_history(
            case_id=case.id,
            actor=context,
            event_type="case.assigned",
            title="Caso asignado",
            description=payload.reason or "Se asigno un responsable interno.",
            metadata={
                "assignedToAdminId": str(assignee.id),
                "assignmentType": payload.assignment_type,
            },
        )
        self._audit(
            context,
            event_type="admin.case.assigned",
            entity_type="assignment",
            entity_id=assignment.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state={
                "assignedToAdminId": str(assignee.id),
                "assignmentType": payload.assignment_type,
                "status": assignment.status,
            },
            metadata={"reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(assignment)
        return {
            "assignmentId": str(assignment.id),
            "caseId": str(case.id),
            "assignedToAdminId": str(assignee.id),
            "assigneeUserId": str(assignee.user_id) if assignee.user_id else None,
            "assignmentType": assignment.assignment_type,
            "status": assignment.status,
        }

    def update_case_status(
        self,
        case_id: str,
        payload: AdminCaseStatusUpdateRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        status_value = payload.admin_status
        if status_value not in ADMIN_CASE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="INVALID_STATUS_TRANSITION",
                message="Estado administrativo invalido.",
            )
        if status_value in REASON_REQUIRED_STATUSES and not _has_text(payload.reason):
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="REVIEW_REASON_REQUIRED",
                message="Debes indicar la razon del cambio de estado.",
            )
        if status_value in DELIVERY_STATUSES:
            if self._has_blocking_items(case.id):
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="BLOCKING_ALERTS_OPEN",
                    message="No se puede aprobar entrega con alertas o tareas bloqueantes abiertas.",
                )
            if not self._payment_confirmed(case.id, case) and not has_admin_permission(
                context,
                "admin.payments.override_unlock",
            ):
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="PAYMENT_REQUIRED_FOR_FULL_DELIVERY",
                    message="El pago debe estar confirmado antes de entregar el analisis completo.",
                )

        queue_item = self._ensure_queue_item(case)
        previous_state = {
            "queue": self._queue_state(queue_item),
            "case": self._case_state(case),
        }
        previous_admin_status = queue_item.admin_status
        queue_item.admin_status = status_value
        queue_item.current_stage = status_value
        queue_item.has_blocking_issue = bool(payload.blocking)
        queue_item.last_activity_at = utc_now()
        queue_item.updated_at = utc_now()
        if status_value in CASE_STATUSES:
            previous_case_status = case.status
            validate_case_transition(
                self.db,
                case,
                new_status=status_value,
                validate_transition=True,
            )
            case.status = status_value
            case.status_reason = payload.reason
            case.current_step, case.next_best_action = step_for_status(status_value)
            case.updated_at = utc_now()
            if previous_case_status != case.status:
                self.db.add(
                    CaseStatusHistory(
                        case_id=case.id,
                        previous_status=previous_case_status,
                        new_status=case.status,
                        reason=payload.reason,
                        changed_by_user_id=context.admin_user.id,
                        changed_by_role="admin",
                        source_module="admin",
                        metadata_json={"adminStatus": status_value},
                    )
                )
        self._record_case_history(
            case_id=case.id,
            actor=context,
            event_type="case.admin_status_changed",
            title="Estado administrativo actualizado",
            description=payload.reason,
            metadata={
                "previousAdminStatus": previous_admin_status,
                "adminStatus": status_value,
                "blocking": payload.blocking,
            },
        )
        self._audit(
            context,
            event_type="admin.case.status_changed",
            entity_type="case_queue_item",
            entity_id=queue_item.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state={
                "queue": self._queue_state(queue_item),
                "case": self._case_state(case),
            },
            metadata={"reason": payload.reason, "blocking": payload.blocking},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "adminStatus": status_value,
            "previousAdminStatus": previous_admin_status,
            "blocking": queue_item.has_blocking_issue,
            "updatedAt": queue_item.updated_at,
        }

    def list_notes(
        self,
        case_id: str,
        *,
        note_type: str | None,
        visibility: str | None,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        query = (
            self.db.query(InternalNote)
            .filter(InternalNote.case_id == case.id)
            .order_by(desc(InternalNote.created_at))
        )
        if note_type:
            query = query.filter(InternalNote.note_type == note_type)
        if visibility:
            query = query.filter(InternalNote.visibility == visibility)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="internal_note",
            entity_id=None,
            case_id=case.id,
            metadata={"noteType": note_type, "visibility": visibility},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"data": [self._note_payload(note) for note in query.all()]}

    def create_note(
        self,
        case_id: str,
        payload: InternalNoteCreateRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if payload.visibility == "published_to_user":
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="DOCUMENT_REVIEW_INVALID",
                message="Las notas internas no se publican desde este endpoint.",
            )
        if payload.visibility == "publishable" and context.admin_user.role not in {
            "super_admin",
            "admin_manager",
        }:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ADMIN_PERMISSION_DENIED",
                message="Solo gerencia administrativa puede marcar notas como publicables.",
            )
        note = InternalNote(
            case_id=case.id,
            admin_user_id=context.admin_user.id,
            note_type=payload.note_type,
            visibility=payload.visibility or "internal",
            body=payload.body,
            related_entity_type=payload.related_entity_type,
            related_entity_id=_uuid_or_none(payload.related_entity_id),
        )
        self.db.add(note)
        self._audit(
            context,
            event_type="admin.note.created",
            entity_type="internal_note",
            entity_id=note.id,
            case_id=case.id,
            new_state={
                "noteType": note.note_type,
                "visibility": note.visibility,
                "relatedEntityType": note.related_entity_type,
                "relatedEntityId": str(note.related_entity_id) if note.related_entity_id else None,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(note)
        return self._note_payload(note)

    def get_documents(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="documents",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_documents"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"data": self._documents_payload(case.id)}

    def review_document(
        self,
        case_id: str,
        document_id: str,
        payload: DocumentReviewRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        document = self._get_document_or_404(case.id, document_id)
        if payload.decision == "invalid" and not _has_text(payload.observations):
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="DOCUMENT_REVIEW_INVALID",
                message="La observacion es obligatoria cuando el documento es invalido.",
            )
        previous_state = self._document_state(document)
        document.validation_status = payload.decision
        if payload.decision in {"invalid", "requires_reupload", "requires_supporting_documents"}:
            document.status = "requires_review"
        else:
            document.status = "reviewed"
        document.updated_at = utc_now()
        decision = self._create_review_decision(
            case_id=case.id,
            context=context,
            review_type="document_review",
            decision=payload.decision,
            reason=payload.observations,
            metadata={
                "documentId": str(document.id),
                "requiresReupload": payload.requires_reupload,
                "requiresSupportingDocuments": payload.requires_supporting_documents,
                "requestedDocuments": payload.requested_documents,
            },
        )
        queue_item = self._ensure_queue_item(case)
        queue_item.document_status = payload.decision
        if payload.requires_reupload:
            queue_item.admin_status = "requires_review"
            queue_item.has_blocking_issue = True
        queue_item.updated_at = utc_now()
        self._audit(
            context,
            event_type="admin.document.reviewed",
            entity_type="document",
            entity_id=document.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state=self._document_state(document),
            metadata={"decisionId": str(decision.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "documentId": str(document.id),
            "caseId": str(case.id),
            "decision": payload.decision,
            "status": document.status,
            "validationStatus": document.validation_status,
        }

    def get_extraction(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="extraction",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_extraction"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._extraction_payload(case.id, include_items=True)

    def correct_extraction_item(
        self,
        case_id: str,
        item_id: str,
        payload: ExtractionCorrectionRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        field = self._get_extraction_field_or_404(case.id, item_id)
        if not _has_text(payload.reason):
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="CORRECTION_REQUIRES_REASON",
                message="La correccion manual requiere una razon.",
            )
        previous_value = field.normalized_value if field.normalized_value is not None else field.display_value
        previous_state = self._extraction_field_payload(field)
        field.normalized_value = payload.new_value
        field.display_value = str(payload.new_value)
        field.status = "corrected"
        field.needs_review = False
        field.updated_at = utc_now()
        correction = UserCorrection(
            case_id=case.id,
            extraction_run_id=field.extraction_run_id,
            extraction_field_id=field.id,
            entity_type=field.entity_type,
            entity_id=field.entity_id,
            field_key=payload.field,
            previous_value=previous_value,
            new_value=payload.new_value,
            reason=payload.reason,
            corrected_by_user_id=context.user.id,
            correction_source="admin",
        )
        self.db.add(correction)
        self._audit(
            context,
            event_type="admin.extraction.corrected",
            entity_type="extraction_field",
            entity_id=field.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state=self._extraction_field_payload(field),
            metadata={
                "correctionId": str(correction.id),
                "reason": payload.reason,
                "recalculateJobSuggested": True,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "itemId": str(field.id),
            "caseId": str(case.id),
            "field": field.field_key,
            "previousValue": previous_value,
            "newValue": field.normalized_value,
            "status": field.status,
        }

    def get_legal_analysis(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="legal_analysis",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_legal_analysis"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "case": self._case_payload(case),
            "preAnalysis": self._pre_analysis_payload(case.id),
            "fullAnalysis": self._full_analysis_payload(case.id),
            "rulesActivated": [
                item for item in self._legal_rules_payload(case.id) if item.get("result") in {"applies", "activated", "true"}
            ],
            "rulesDiscarded": [
                item for item in self._legal_rules_payload(case.id) if item.get("result") in {"discarded", "does_not_apply", "false"}
            ],
            "alerts": [self._alert_payload(alert) for alert in self._alerts_for_case(case.id)],
        }

    def review_legal_analysis(
        self,
        case_id: str,
        payload: ReviewDecisionRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if payload.decision == "approved" and self._has_unresolved_alerts(
            case.id,
            sources={"legal_classifier", "legal_rules_engine"},
            severities=BLOCKING_ALERT_SEVERITIES,
        ):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="BLOCKING_ALERTS_OPEN",
                message="No se puede aprobar analisis juridico con alertas criticas sin resolver.",
            )
        full_analysis = self._latest_full_analysis(case.id)
        if (
            full_analysis is not None
            and full_analysis.requires_human_review
            and payload.decision == "approved"
            and not payload.requires_human_review
        ):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="INVALID_STATUS_TRANSITION",
                message="El caso requiere revision humana explicita antes de aprobar.",
            )
        decision = self._create_review_decision(
            case_id=case.id,
            context=context,
            review_type="legal_analysis",
            decision=payload.decision,
            reason=payload.reason,
            metadata={
                "requiresHumanReview": payload.requires_human_review,
                "resolvedAlertIds": payload.resolved_alert_ids,
            },
        )
        self._resolve_alert_ids(case.id, payload.resolved_alert_ids, context)
        queue_item = self._ensure_queue_item(case)
        queue_item.legal_review_status = payload.decision
        queue_item.admin_status = "internal_review_pending" if payload.decision != "approved" else "completed"
        queue_item.updated_at = utc_now()
        self._audit(
            context,
            event_type="admin.analysis.reviewed",
            entity_type="admin_review_decision",
            entity_id=decision.id,
            case_id=case.id,
            new_state=self._review_decision_payload(decision),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._review_decision_payload(decision)

    def get_calculations(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="calculations",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_calculations"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"data": self._calculations_payload(case.id), "scenarios": self._scenarios_payload(case.id)}

    def review_calculations(
        self,
        case_id: str,
        payload: ReviewDecisionRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        decision = self._create_review_decision(
            case_id=case.id,
            context=context,
            review_type="calculation",
            decision=payload.decision,
            reason=payload.reason,
            metadata={"blocking": payload.blocking},
        )
        queue_item = self._ensure_queue_item(case)
        queue_item.calculation_review_status = payload.decision
        queue_item.has_blocking_issue = queue_item.has_blocking_issue or payload.blocking
        queue_item.updated_at = utc_now()
        self._audit(
            context,
            event_type="admin.calculation.reviewed",
            entity_type="admin_review_decision",
            entity_id=decision.id,
            case_id=case.id,
            new_state=self._review_decision_payload(decision),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._review_decision_payload(decision)

    def get_reports(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="reports",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_reports"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"data": self._reports_payload(case.id)}

    def approve_report(
        self,
        case_id: str,
        report_id: str,
        payload: ReportApprovalRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        report = self._get_report_or_404(case.id, report_id)
        if payload.visible_to_user and not self._payment_confirmed(case.id, case):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_REQUIRED_FOR_FULL_DELIVERY",
                message="No se puede hacer visible un informe completo sin pago confirmado.",
            )
        previous_state = self._report_payload(report)
        report.status = payload.decision
        report.visibility = "user" if payload.visible_to_user else "internal"
        report.approved_by = context.user.id if payload.decision == "approved" else None
        report.approved_at = utc_now() if payload.decision == "approved" else None
        report.review_reason = payload.reason
        report.updated_at = utc_now()
        if report.current_version_id is not None and payload.decision == "approved":
            version = self.db.get(ReportVersion, report.current_version_id)
            if version is not None:
                version.status = "approved"
        decision = self._create_review_decision(
            case_id=case.id,
            context=context,
            review_type="report",
            decision=payload.decision,
            reason=payload.reason,
            metadata={"reportId": str(report.id), "visibleToUser": payload.visible_to_user},
        )
        self._audit(
            context,
            event_type="admin.report.approved",
            entity_type="report",
            entity_id=report.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state=self._report_payload(report),
            metadata={"decisionId": str(decision.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._report_payload(report)

    def get_legal_drafts(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._audit(
            context,
            event_type="backoffice_admin.viewed",
            entity_type="legal_drafts",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_legal_drafts"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"data": self._legal_drafts_payload(case.id)}

    def review_legal_draft(
        self,
        case_id: str,
        draft_id: str,
        payload: LegalDraftReviewRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        draft = self._get_legal_draft_or_404(case.id, draft_id)
        previous_state = self._legal_draft_payload(draft)
        draft.status = payload.decision
        draft.last_edited_by = context.user.id
        draft.updated_at = utc_now()
        if payload.quality_checklist:
            self.db.add(
                DraftQualityCheck(
                    draft_id=draft.id,
                    overall_status=payload.decision,
                    score=None,
                    checks=[
                        {"key": key, "passed": value}
                        for key, value in payload.quality_checklist.items()
                    ],
                    critical_warnings=[],
                )
            )
        decision = self._create_review_decision(
            case_id=case.id,
            context=context,
            review_type="legal_draft",
            decision=payload.decision,
            reason=payload.reason,
            metadata={"draftId": str(draft.id), "qualityChecklist": payload.quality_checklist},
        )
        event_type = "admin.legal_draft.approved" if payload.decision == "approved" else "admin.legal_draft.reviewed"
        self._audit(
            context,
            event_type=event_type,
            entity_type="legal_draft",
            entity_id=draft.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state=self._legal_draft_payload(draft),
            metadata={"decisionId": str(decision.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._legal_draft_payload(draft)

    def get_payment_status(
        self,
        case_id: str,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        payload = self._payment_summary(case.id)
        self._audit(
            context,
            event_type="admin.payment.viewed",
            entity_type="payment_status",
            entity_id=None,
            case_id=case.id,
            metadata={"surface": "admin_payment_status"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return payload

    def override_unlock(
        self,
        case_id: str,
        payload: OverrideUnlockRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if context.admin_user.role != "super_admin" and not has_admin_permission(
            context,
            "admin.payments.override_unlock",
        ):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ADMIN_PERMISSION_DENIED",
                message="No tienes permiso para desbloquear analisis completo.",
            )
        previous_state = self._case_state(case)
        if payload.unlock_full_analysis:
            previous_case_status = case.status
            validate_case_transition(
                self.db,
                case,
                new_status="full_analysis_unlocked",
                validate_transition=True,
            )
            case.status = "full_analysis_unlocked"
            case.status_reason = payload.reason
            case.current_step, case.next_best_action = step_for_status("full_analysis_unlocked")
            case.updated_at = utc_now()
            if previous_case_status != case.status:
                self.db.add(
                    CaseStatusHistory(
                        case_id=case.id,
                        previous_status=previous_case_status,
                        new_status=case.status,
                        reason=payload.reason,
                        changed_by_user_id=context.admin_user.id,
                        changed_by_role="admin",
                        source_module="admin",
                        metadata_json={"unlockFullAnalysis": True},
                    )
                )
        queue_item = self._ensure_queue_item(case)
        queue_item.payment_status = "admin_override_unlocked"
        queue_item.admin_status = "payment_confirmed"
        queue_item.updated_at = utc_now()
        self._audit(
            context,
            event_type="admin.payment.unlock_overridden",
            entity_type="case",
            entity_id=case.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state=self._case_state(case),
            metadata={
                "reason": payload.reason,
                "unlockFullAnalysis": payload.unlock_full_analysis,
                "highVisibility": True,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "unlockFullAnalysis": payload.unlock_full_analysis,
            "status": case.status,
            "paymentStatus": queue_item.payment_status,
        }

    def get_ai_alerts(self, case_id: str) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        return {"data": [self._alert_payload(alert) for alert in self._alerts_for_case(case.id)]}

    def resolve_ai_alert(
        self,
        case_id: str,
        alert_id: str,
        payload: ResolveAiAlertRequest,
        *,
        context: AdminContext,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        alert_uuid = _parse_uuid(alert_id, code="AI_ALERT_NOT_FOUND")
        alert = (
            self.db.query(AiConfidenceAlert)
            .filter(AiConfidenceAlert.id == alert_uuid, AiConfidenceAlert.case_id == case.id)
            .one_or_none()
        )
        if alert is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="AI_ALERT_NOT_FOUND",
                message="Alerta IA no encontrada.",
            )
        required_permission = _permission_for_alert_source(alert.source)
        if required_permission and not has_admin_permission(context, required_permission):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="ADMIN_PERMISSION_DENIED",
                message="No tienes permiso para resolver esta alerta.",
            )
        previous_state = self._alert_payload(alert)
        alert.resolved = True
        alert.resolved_by_admin_id = context.admin_user.id
        alert.resolved_at = utc_now()
        self._audit(
            context,
            event_type="admin.ai_alert.resolved",
            entity_type="ai_confidence_alert",
            entity_id=alert.id,
            case_id=case.id,
            previous_state=previous_state,
            new_state=self._alert_payload(alert),
            metadata={
                "resolution": payload.resolution,
                "keepWarningInUserReport": payload.keep_warning_in_user_report,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._alert_payload(alert)

    def _case_query(
        self,
        *,
        from_date: datetime | None,
        to_date: datetime | None,
    ):
        query = self.db.query(LaboraCase).filter(LaboraCase.deleted_at.is_(None))
        if from_date:
            query = query.filter(LaboraCase.created_at >= from_date)
        if to_date:
            query = query.filter(LaboraCase.created_at <= to_date)
        return query

    def _filter_assignee(self, query, assigned_to: str, context: AdminContext):
        if assigned_to == "me":
            return query.filter(CaseQueueItem.assigned_to_admin_id == context.admin_user.id)
        if assigned_to == "unassigned":
            return query.filter(or_(CaseQueueItem.id.is_(None), CaseQueueItem.assigned_to_admin_id.is_(None)))
        assigned_uuid = _parse_uuid(assigned_to, code="ADMIN_USER_NOT_FOUND")
        admin_user = self.db.get(AdminUser, assigned_uuid)
        if admin_user is None:
            admin_user = (
                self.db.query(AdminUser)
                .filter(AdminUser.user_id == assigned_uuid)
                .one_or_none()
            )
        if admin_user is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ADMIN_USER_NOT_FOUND",
                message="Administrador asignado no encontrado.",
            )
        return query.filter(CaseQueueItem.assigned_to_admin_id == admin_user.id)

    def _sort_case_query(self, query, sort: str):
        if sort == "created_desc":
            return query.order_by(desc(LaboraCase.created_at))
        if sort == "sla_asc":
            return query.order_by(CaseQueueItem.sla_due_at.asc(), desc(LaboraCase.updated_at))
        if sort == "priority_desc":
            priority_rank = sql_case(
                (CaseQueueItem.priority == "urgent", 4),
                (CaseQueueItem.priority == "high", 3),
                (CaseQueueItem.priority == "normal", 2),
                (CaseQueueItem.priority == "low", 1),
                else_=2,
            )
            return query.order_by(
                desc(priority_rank),
                desc(func.coalesce(CaseQueueItem.last_activity_at, LaboraCase.updated_at)),
            )
        return query.order_by(desc(func.coalesce(CaseQueueItem.last_activity_at, LaboraCase.updated_at)))

    def _case_queue_payload(
        self,
        case: LaboraCase,
        item: CaseQueueItem | None,
        assignee: AdminUser | None,
    ) -> dict[str, Any]:
        current_stage = item.current_stage if item else _stage_for_case(case)
        admin_status = item.admin_status if item else _admin_status_for_case(case)
        priority = item.priority if item else "normal"
        payment_status = item.payment_status if item and item.payment_status else _payment_status_for_case(case)
        document_status = item.document_status if item else None
        analysis_status = item.analysis_status if item else _analysis_status_for_case(case)
        last_activity = item.last_activity_at if item and item.last_activity_at else case.updated_at
        assigned_payload = None
        if assignee is not None:
            assigned_payload = {"id": str(assignee.id), "name": assignee.full_name}
        has_low_confidence = (
            bool(item.has_low_confidence_ai)
            if item
            else self._case_has_low_confidence(case.id)
        )
        has_blocking_issue = (
            bool(item.has_blocking_issue)
            if item
            else self._has_blocking_items(case.id)
        )
        return {
            "caseId": str(case.id),
            "id": str(case.id),
            "caseNumber": case.case_number,
            "holderName": _holder_full_name(case),
            "holderFullName": _holder_full_name(case),
            "currentStage": current_stage,
            "status": case.status,
            "adminStatus": admin_status,
            "priority": priority,
            "assignedTo": assigned_payload,
            "paymentStatus": payment_status,
            "documentStatus": document_status,
            "analysisStatus": analysis_status,
            "legalReviewStatus": item.legal_review_status if item else None,
            "calculationReviewStatus": item.calculation_review_status if item else None,
            "hasLowConfidenceAi": has_low_confidence,
            "hasBlockingIssue": has_blocking_issue,
            "slaDueAt": item.sla_due_at if item else None,
            "lastActivityAt": last_activity,
            "updatedAt": case.updated_at,
        }

    def _resolve_admin_assignee(self, value: str) -> AdminUser:
        parsed_id = _parse_uuid(value, code="ADMIN_USER_NOT_FOUND")
        admin_user = self.db.get(AdminUser, parsed_id)
        if admin_user is not None:
            return admin_user
        user = self.db.get(User, parsed_id)
        if user is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ADMIN_USER_NOT_FOUND",
                message="Administrador asignado no encontrado.",
            )
        admin_user = (
            self.db.query(AdminUser)
            .filter(or_(AdminUser.user_id == user.id, AdminUser.email == user.email.lower()))
            .one_or_none()
        )
        if admin_user is not None:
            return admin_user
        role = user.role if user.role in {"super_admin", "admin_manager", "document_reviewer", "legal_reviewer", "calculation_reviewer", "support_agent", "read_only_admin"} else "legal_reviewer"
        admin_user = AdminUser(
            user_id=user.id,
            full_name=(user.full_name or user.email)[:180],
            email=user.email.lower(),
            role=role,
            status="active",
        )
        self.db.add(admin_user)
        self.db.flush()
        return admin_user

    def _ensure_queue_item(self, case: LaboraCase) -> CaseQueueItem:
        item = self._queue_item_for_case(case.id)
        if item is not None:
            item.case_number = case.case_number
            item.user_id = case.owner_user_id
            return item
        item = CaseQueueItem(
            case_id=case.id,
            case_number=case.case_number,
            user_id=case.owner_user_id,
            current_stage=_stage_for_case(case),
            admin_status=_admin_status_for_case(case),
            priority="normal",
            payment_status=_payment_status_for_case(case),
            analysis_status=_analysis_status_for_case(case),
            has_low_confidence_ai=self._case_has_low_confidence(case.id),
            has_blocking_issue=self._has_blocking_items(case.id),
            last_activity_at=case.updated_at,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def _queue_item_for_case(self, case_id: uuid.UUID) -> CaseQueueItem | None:
        return (
            self.db.query(CaseQueueItem)
            .filter(CaseQueueItem.case_id == case_id)
            .one_or_none()
        )

    def _queue_items_for_cases(self, case_ids: list[uuid.UUID]) -> dict[uuid.UUID, CaseQueueItem]:
        if not case_ids:
            return {}
        return {
            item.case_id: item
            for item in self.db.query(CaseQueueItem)
            .filter(CaseQueueItem.case_id.in_(case_ids))
            .all()
        }

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        parsed_id = _parse_uuid(case_id, code="CASE_NOT_FOUND")
        case = self.db.get(LaboraCase, parsed_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_document_or_404(self, case_id: uuid.UUID, document_id: str) -> Document:
        parsed_id = _parse_uuid(document_id, code="DOCUMENT_NOT_FOUND")
        document = (
            self.db.query(Document)
            .filter(Document.id == parsed_id, Document.case_id == case_id, Document.deleted_at.is_(None))
            .one_or_none()
        )
        if document is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DOCUMENT_NOT_FOUND",
                message="Documento no encontrado.",
            )
        return document

    def _get_extraction_field_or_404(self, case_id: uuid.UUID, item_id: str) -> ExtractionField:
        parsed_id = _parse_uuid(item_id, code="EXTRACTION_ITEM_NOT_FOUND")
        field = (
            self.db.query(ExtractionField)
            .filter(ExtractionField.id == parsed_id, ExtractionField.case_id == case_id)
            .one_or_none()
        )
        if field is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="EXTRACTION_ITEM_NOT_FOUND",
                message="Dato extraido no encontrado.",
            )
        return field

    def _get_report_or_404(self, case_id: uuid.UUID, report_id: str) -> Report:
        parsed_id = _parse_uuid(report_id, code="CASE_NOT_FOUND")
        report = (
            self.db.query(Report)
            .filter(Report.id == parsed_id, Report.case_id == case_id, Report.deleted_at.is_(None))
            .one_or_none()
        )
        if report is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Informe no encontrado.",
            )
        return report

    def _get_legal_draft_or_404(self, case_id: uuid.UUID, draft_id: str) -> LegalDraft:
        parsed_id = _parse_uuid(draft_id, code="CASE_NOT_FOUND")
        draft = (
            self.db.query(LegalDraft)
            .filter(LegalDraft.id == parsed_id, LegalDraft.case_id == case_id)
            .one_or_none()
        )
        if draft is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Escrito juridico no encontrado.",
            )
        return draft

    def _notes_for_case(self, case_id: uuid.UUID) -> list[InternalNote]:
        return (
            self.db.query(InternalNote)
            .filter(InternalNote.case_id == case_id)
            .order_by(desc(InternalNote.created_at))
            .all()
        )

    def _alerts_for_case(self, case_id: uuid.UUID) -> list[AiConfidenceAlert]:
        return (
            self.db.query(AiConfidenceAlert)
            .filter(AiConfidenceAlert.case_id == case_id)
            .order_by(AiConfidenceAlert.resolved.asc(), desc(AiConfidenceAlert.created_at))
            .all()
        )

    def _review_tasks_for_case(self, case_id: uuid.UUID) -> list[AdminReviewTask]:
        return (
            self.db.query(AdminReviewTask)
            .filter(AdminReviewTask.case_id == case_id)
            .order_by(AdminReviewTask.completed_at.asc(), desc(AdminReviewTask.created_at))
            .all()
        )

    def _case_history_for_case(self, case_id: uuid.UUID) -> list[CaseHistoryEvent]:
        return (
            self.db.query(CaseHistoryEvent)
            .filter(CaseHistoryEvent.case_id == case_id)
            .order_by(desc(CaseHistoryEvent.created_at))
            .limit(50)
            .all()
        )

    def _latest_pre_analysis(self, case_id: uuid.UUID) -> PreAnalysis | None:
        return (
            self.db.query(PreAnalysis)
            .filter(PreAnalysis.case_id == case_id, PreAnalysis.deleted_at.is_(None))
            .order_by(desc(PreAnalysis.created_at))
            .first()
        )

    def _latest_full_analysis(self, case_id: uuid.UUID) -> FullAnalysis | None:
        return (
            self.db.query(FullAnalysis)
            .filter(FullAnalysis.case_id == case_id)
            .order_by(desc(FullAnalysis.version), desc(FullAnalysis.created_at))
            .first()
        )

    def _documents_payload(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        documents = (
            self.db.query(Document)
            .filter(Document.case_id == case_id, Document.deleted_at.is_(None))
            .order_by(desc(Document.created_at))
            .all()
        )
        return [self._document_payload(document) for document in documents]

    def _document_payload(self, document: Document) -> dict[str, Any]:
        pages = (
            self.db.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number.asc())
            .all()
        )
        latest_validation = (
            self.db.query(DocumentValidation)
            .filter(DocumentValidation.document_id == document.id)
            .order_by(desc(DocumentValidation.created_at))
            .first()
        )
        return {
            "id": str(document.id),
            "documentId": str(document.id),
            "caseId": str(document.case_id),
            "originalFilename": document.original_filename,
            "displayName": document.display_name,
            "mimeType": document.mime_type,
            "sizeBytes": document.size_bytes,
            "status": document.status,
            "validationStatus": document.validation_status,
            "classificationSource": document.classification_source,
            "aiConfidence": _decimal(document.ai_confidence),
            "isPrimary": document.is_primary,
            "isDuplicate": document.is_duplicate,
            "pageCount": document.page_count,
            "ocrStatus": _aggregate_ocr_status(pages),
            "pages": [
                {
                    "pageNumber": page.page_number,
                    "ocrStatus": page.ocr_status,
                    "textConfidence": _decimal(page.text_confidence),
                    "qualityScore": _decimal(page.quality_score),
                    "warnings": page.warnings or [],
                }
                for page in pages
            ],
            "latestValidation": {
                "status": latest_validation.status,
                "result": latest_validation.result,
                "score": _decimal(latest_validation.score),
                "warnings": latest_validation.warnings,
                "errors": latest_validation.errors,
                "createdAt": latest_validation.created_at,
            }
            if latest_validation
            else None,
            "securePreviewUrl": None,
            "createdAt": document.created_at,
            "updatedAt": document.updated_at,
        }

    def _extraction_payload(self, case_id: uuid.UUID, *, include_items: bool) -> dict[str, Any]:
        run = (
            self.db.query(ExtractionRun)
            .filter(ExtractionRun.case_id == case_id)
            .order_by(desc(ExtractionRun.created_at))
            .first()
        )
        if run is None:
            return {
                "run": None,
                "employers": [],
                "laborPeriods": [],
                "contributionWeeks": [],
                "salaryBases": [],
                "novelties": [],
                "gaps": [],
                "issues": [],
                "fields": [],
            }
        payload = {
            "run": {
                "id": str(run.id),
                "status": run.status,
                "confirmationStatus": run.confirmation_status,
                "source": run.source,
                "confidenceAvg": _decimal(run.confidence_avg),
                "lowConfidenceCount": run.low_confidence_count,
                "issuesCount": run.issues_count,
                "createdAt": run.created_at,
                "updatedAt": run.updated_at,
            },
            "employers": [self._employer_payload(item) for item in self.db.query(Employer).filter(Employer.case_id == case_id).all()],
            "laborPeriods": [self._labor_period_payload(item) for item in self.db.query(LaborPeriod).filter(LaborPeriod.case_id == case_id).all()],
            "contributionWeeks": [self._contribution_week_payload(item) for item in self.db.query(ContributionWeek).filter(ContributionWeek.case_id == case_id).limit(500).all()],
            "salaryBases": [self._salary_base_payload(item) for item in self.db.query(SalaryBase).filter(SalaryBase.case_id == case_id).limit(500).all()],
            "novelties": [self._novelty_payload(item) for item in self.db.query(LaborNovelty).filter(LaborNovelty.case_id == case_id).all()],
            "gaps": [self._gap_payload(item) for item in self.db.query(ContributionGap).filter(ContributionGap.case_id == case_id).all()],
            "issues": [self._extraction_issue_payload(item) for item in self.db.query(ExtractionIssue).filter(ExtractionIssue.case_id == case_id).all()],
        }
        payload["fields"] = (
            [
                self._extraction_field_payload(field)
                for field in self.db.query(ExtractionField)
                .filter(ExtractionField.case_id == case_id)
                .order_by(ExtractionField.entity_type.asc(), ExtractionField.field_key.asc())
                .all()
            ]
            if include_items
            else []
        )
        return payload

    def _questionnaire_payload(self, case_id: uuid.UUID) -> dict[str, Any] | None:
        session = (
            self.db.query(CaseQuestionnaireSession)
            .filter(CaseQuestionnaireSession.case_id == case_id)
            .order_by(desc(CaseQuestionnaireSession.created_at))
            .first()
        )
        if session is None:
            return None
        answers = (
            self.db.query(QuestionnaireAnswer)
            .filter(QuestionnaireAnswer.session_id == session.id)
            .order_by(QuestionnaireAnswer.question_code.asc())
            .all()
        )
        return {
            "id": str(session.id),
            "status": session.status,
            "completionPercentage": _decimal(session.completion_percentage),
            "currentSection": session.current_section,
            "requiresReviewReason": session.requires_review_reason,
            "answers": [
                {
                    "questionCode": answer.question_code,
                    "value": answer.value,
                    "valueText": answer.value_text,
                    "source": answer.source,
                    "confidence": _decimal(answer.confidence),
                    "isCritical": answer.is_critical,
                    "requiresReview": answer.requires_review,
                }
                for answer in answers
            ],
        }

    def _pre_analysis_payload(self, case_id: uuid.UUID) -> dict[str, Any] | None:
        item = self._latest_pre_analysis(case_id)
        if item is None:
            return None
        issues = (
            self.db.query(PreIssue)
            .filter(PreIssue.pre_analysis_id == item.id)
            .order_by(PreIssue.sort_order.asc())
            .all()
        )
        return {
            "id": str(item.id),
            "status": item.status,
            "trafficLight": item.traffic_light,
            "viabilityLevel": item.viability_level,
            "completionScore": _decimal(item.completion_score),
            "confidence": _decimal(item.confidence),
            "limitedSummary": item.limited_summary,
            "valueDetectedTitle": item.value_detected_title,
            "valueDetectedSummary": item.value_detected_summary,
            "requiresHumanReview": item.status == "requires_review",
            "issues": [
                {
                    "id": str(issue.id),
                    "issueType": issue.issue_type,
                    "severity": issue.severity,
                    "title": issue.title,
                    "publicSummary": issue.public_summary,
                    "confidence": _decimal(issue.confidence),
                }
                for issue in issues
            ],
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }

    def _full_analysis_payload(self, case_id: uuid.UUID) -> dict[str, Any] | None:
        item = self._latest_full_analysis(case_id)
        if item is None:
            return None
        return {
            "id": str(item.id),
            "status": item.status,
            "version": item.version,
            "summary": item.summary,
            "executiveResult": item.executive_result,
            "legalConclusion": item.legal_conclusion,
            "recommendedRoute": item.recommended_route,
            "viabilityLevel": item.viability_level,
            "confidenceGlobal": _decimal(item.confidence_global),
            "requiresHumanReview": item.requires_human_review,
            "humanReviewReason": item.human_review_reason,
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }

    def _legal_rules_payload(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        rules = (
            self.db.query(LegalRuleResult)
            .filter(LegalRuleResult.case_id == case_id)
            .order_by(LegalRuleResult.rule_category.asc(), LegalRuleResult.rule_code.asc())
            .all()
        )
        return [
            {
                "id": str(rule.id),
                "ruleCode": rule.rule_code,
                "ruleName": rule.rule_name,
                "ruleVersion": rule.rule_version,
                "ruleCategory": rule.rule_category,
                "result": rule.result,
                "resultDetail": rule.result_detail,
                "explanation": rule.explanation,
                "confidence": _decimal(rule.confidence),
                "requiresReview": rule.requires_review,
                "sourceRefs": rule.source_refs,
            }
            for rule in rules
        ]

    def _calculations_payload(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        calculations = (
            self.db.query(CalculationResult)
            .filter(CalculationResult.case_id == case_id)
            .order_by(CalculationResult.calculation_type.asc(), CalculationResult.calculation_code.asc())
            .all()
        )
        return [
            {
                "id": str(item.id),
                "calculationCode": item.calculation_code,
                "calculationName": item.calculation_name,
                "calculationVersion": item.calculation_version,
                "calculationType": item.calculation_type,
                "inputValues": item.input_values,
                "formulaRef": item.formula_ref,
                "formulaExpression": item.formula_expression,
                "sourceRefs": item.source_refs,
                "resultValue": _decimal(item.result_value),
                "resultUnit": item.result_unit,
                "resultDetail": item.result_detail,
                "confidence": _decimal(item.confidence),
            }
            for item in calculations
        ]

    def _scenarios_payload(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        scenarios = self.db.query(Scenario).filter(Scenario.case_id == case_id).all()
        return [
            {
                "id": str(item.id),
                "scenarioType": item.scenario_type,
                "name": item.name,
                "description": item.description,
                "amountEstimated": _decimal(item.amount_estimated),
                "weeksEstimated": _decimal(item.weeks_estimated),
                "retroactiveEstimated": _decimal(item.retroactive_estimated),
                "differenceVsRecognized": _decimal(item.difference_vs_recognized),
                "confidence": _decimal(item.confidence),
                "legalBasisRefs": item.legal_basis_refs,
                "calculationRefs": item.calculation_refs,
            }
            for item in scenarios
        ]

    def _reports_payload(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        reports = (
            self.db.query(Report)
            .filter(Report.case_id == case_id, Report.deleted_at.is_(None))
            .order_by(desc(Report.created_at))
            .all()
        )
        return [self._report_payload(report) for report in reports]

    def _report_payload(self, report: Report) -> dict[str, Any]:
        versions = (
            self.db.query(ReportVersion)
            .filter(ReportVersion.report_id == report.id)
            .order_by(desc(ReportVersion.version_number))
            .all()
        )
        return {
            "id": str(report.id),
            "reportId": str(report.id),
            "caseId": str(report.case_id),
            "reportType": report.report_type,
            "title": report.title,
            "status": report.status,
            "visibility": report.visibility,
            "currentVersionId": str(report.current_version_id) if report.current_version_id else None,
            "aiConfidence": _decimal(report.ai_confidence),
            "requiresHumanReview": report.requires_human_review,
            "reviewReason": report.review_reason,
            "approvedBy": str(report.approved_by) if report.approved_by else None,
            "approvedAt": report.approved_at,
            "versions": [
                {
                    "id": str(version.id),
                    "versionNumber": version.version_number,
                    "status": version.status,
                    "changeSummary": version.change_summary,
                    "createdByRole": version.created_by_role,
                    "createdAt": version.created_at,
                }
                for version in versions
            ],
        }

    def _legal_drafts_payload(self, case_id: uuid.UUID) -> list[dict[str, Any]]:
        drafts = (
            self.db.query(LegalDraft)
            .filter(LegalDraft.case_id == case_id)
            .order_by(desc(LegalDraft.created_at))
            .all()
        )
        return [self._legal_draft_payload(draft) for draft in drafts]

    def _legal_draft_payload(self, draft: LegalDraft) -> dict[str, Any]:
        action = self.db.get(LegalAction, draft.legal_action_id)
        return {
            "id": str(draft.id),
            "draftId": str(draft.id),
            "caseId": str(draft.case_id),
            "legalActionId": str(draft.legal_action_id),
            "actionType": action.action_type if action else None,
            "title": draft.title,
            "status": draft.status,
            "generationMode": draft.generation_mode,
            "qualityScore": _decimal(draft.quality_score),
            "professionalReviewLevel": draft.professional_review_level,
            "isLocked": draft.is_locked,
            "currentVersionNumber": draft.current_version_number,
            "createdAt": draft.created_at,
            "updatedAt": draft.updated_at,
        }

    def _payment_summary(self, case_id: uuid.UUID) -> dict[str, Any]:
        order = (
            self.db.query(Order)
            .filter(Order.case_id == case_id)
            .order_by(desc(Order.created_at))
            .first()
        )
        payment = (
            self.db.query(Payment)
            .filter(Payment.case_id == case_id)
            .order_by(desc(Payment.created_at))
            .first()
        )
        case = self.db.get(LaboraCase, case_id)
        return {
            "caseId": str(case_id),
            "paymentConfirmed": self._payment_confirmed(case_id, case),
            "fullAnalysisUnlocked": bool(case and case.status in PAYMENT_CONFIRMED_STATUSES),
            "order": {
                "id": str(order.id),
                "status": order.status,
                "currency": order.currency,
                "totalAmount": order.total_amount,
                "productCode": order.product_code,
                "expiresAt": order.expires_at,
                "paidAt": order.paid_at,
            }
            if order
            else None,
            "payment": {
                "id": str(payment.id),
                "status": payment.status,
                "provider": payment.provider,
                "providerStatus": payment.provider_status,
                "amount": payment.amount,
                "currency": payment.currency,
                "paymentMethod": payment.payment_method,
                "approvedAt": payment.approved_at,
            }
            if payment
            else None,
        }

    def _consent_summary(self, user_id: uuid.UUID) -> dict[str, Any]:
        accepted = (
            self.db.query(UserConsent)
            .filter(UserConsent.user_id == user_id, UserConsent.accepted.is_(True))
            .count()
        )
        return {"acceptedCount": accepted, "hasAcceptedConsents": accepted > 0}

    def _case_payload(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "ownerUserId": str(case.owner_user_id),
            "caseTypeRequested": case.case_type_requested,
            "caseTypeSuggested": case.case_type_suggested,
            "pensionFundOrEntity": case.pension_fund_or_entity,
            "situationType": case.situation_type,
            "status": case.status,
            "statusReason": case.status_reason,
            "currentStep": case.current_step,
            "nextBestAction": case.next_best_action,
            "createdAt": case.created_at,
            "updatedAt": case.updated_at,
        }

    def _holder_payload(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "holderType": case.holder_type,
            "firstName": case.holder_first_name,
            "lastName": case.holder_last_name,
            "fullName": _holder_full_name(case),
            "documentType": case.holder_document_type,
            "documentNumber": case.holder_document_number,
            "birthDate": case.holder_birth_date,
            "email": case.holder_email,
            "phone": case.holder_phone,
            "actingAsThirdParty": case.acting_as_third_party,
            "thirdPartyRelationship": case.third_party_relationship,
            "thirdPartyAuthorizationStatus": case.third_party_authorization_status,
        }

    def _user_payload(self, user: User | None) -> dict[str, Any] | None:
        if user is None:
            return None
        return {
            "id": str(user.id),
            "email": user.email,
            "fullName": user.full_name,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "role": user.role,
            "status": user.status,
        }

    def _create_review_decision(
        self,
        *,
        case_id: uuid.UUID,
        context: AdminContext,
        review_type: str,
        decision: str,
        reason: str | None,
        metadata: dict[str, Any] | None,
    ) -> AdminReviewDecision:
        if decision in {"rejected", "returned_for_correction", "requires_more_documents", "blocked"} and not _has_text(reason):
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="REVIEW_REASON_REQUIRED",
                message="Toda revision negativa requiere razon.",
            )
        item = AdminReviewDecision(
            case_id=case_id,
            review_type=review_type,
            decision=decision,
            decision_reason=reason,
            admin_user_id=context.admin_user.id,
            metadata_json=_json_safe(metadata) if metadata else None,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def _review_decision_payload(self, item: AdminReviewDecision) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "reviewType": item.review_type,
            "decision": item.decision,
            "decisionReason": item.decision_reason,
            "adminUserId": str(item.admin_user_id),
            "metadata": item.metadata_json,
            "createdAt": item.created_at,
        }

    def _create_or_update_case_owner(self, *, case_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
        existing = (
            self.db.query(CaseOwner)
            .filter(CaseOwner.case_id == case_id, CaseOwner.user_id == user_id, CaseOwner.role == role)
            .one_or_none()
        )
        if existing is not None:
            existing.permissions = {"view_case": True, "view_history": True}
            return
        self.db.add(
            CaseOwner(
                case_id=case_id,
                user_id=user_id,
                role=role,
                permissions={"view_case": True, "view_history": True},
            )
        )

    def _record_case_history(
        self,
        *,
        case_id: uuid.UUID,
        actor: AdminContext,
        event_type: str,
        title: str,
        description: str | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.db.add(
            CaseHistoryEvent(
                case_id=case_id,
                event_type=event_type,
                title=title,
                description=description,
                visibility="internal",
                severity="info",
                created_by_user_id=actor.user.id,
                metadata_json=_json_safe(metadata) if metadata else None,
            )
        )

    def _audit(
        self,
        context: AdminContext,
        *,
        event_type: str,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        case_id: uuid.UUID | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AdminAuditEvent:
        event = AdminAuditEvent(
            actor_admin_id=context.admin_user.id,
            case_id=case_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata_json=_json_safe(metadata) if metadata else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(event)
        self.db.flush()
        return event

    def _payment_confirmed(self, case_id: uuid.UUID, case: LaboraCase | None) -> bool:
        if case is not None and case.status in PAYMENT_CONFIRMED_STATUSES:
            return True
        paid_order = (
            self.db.query(Order.id)
            .filter(Order.case_id == case_id, Order.status.in_(["paid", "refunded"]))
            .first()
        )
        if paid_order is not None:
            return True
        approved_payment = (
            self.db.query(Payment.id)
            .filter(Payment.case_id == case_id, Payment.status == "approved")
            .first()
        )
        return approved_payment is not None

    def _has_blocking_items(self, case_id: uuid.UUID) -> bool:
        return (
            self._has_unresolved_alerts(
                case_id,
                sources=None,
                severities=BLOCKING_ALERT_SEVERITIES,
            )
            or self.db.query(AdminReviewTask.id)
            .filter(
                AdminReviewTask.case_id == case_id,
                AdminReviewTask.blocking.is_(True),
                AdminReviewTask.status.in_(OPEN_TASK_STATUSES),
                AdminReviewTask.completed_at.is_(None),
            )
            .first()
            is not None
        )

    def _case_has_low_confidence(self, case_id: uuid.UUID) -> bool:
        if (
            self.db.query(AiConfidenceAlert.id)
            .filter(AiConfidenceAlert.case_id == case_id, AiConfidenceAlert.resolved.is_(False))
            .first()
            is not None
        ):
            return True
        pre_analysis = self._latest_pre_analysis(case_id)
        if pre_analysis is not None and pre_analysis.confidence is not None:
            return Decimal(pre_analysis.confidence) < Decimal("0.6000")
        full_analysis = self._latest_full_analysis(case_id)
        return bool(full_analysis and full_analysis.requires_human_review)

    def _has_unresolved_alerts(
        self,
        case_id: uuid.UUID,
        *,
        sources: set[str] | None,
        severities: set[str],
    ) -> bool:
        query = self.db.query(AiConfidenceAlert.id).filter(
            AiConfidenceAlert.case_id == case_id,
            AiConfidenceAlert.resolved.is_(False),
            AiConfidenceAlert.severity.in_(severities),
        )
        if sources:
            query = query.filter(AiConfidenceAlert.source.in_(sources))
        return query.first() is not None

    def _resolve_alert_ids(
        self,
        case_id: uuid.UUID,
        alert_ids: list[str],
        context: AdminContext,
    ) -> None:
        for alert_id in alert_ids:
            parsed_id = _parse_uuid(alert_id, code="AI_ALERT_NOT_FOUND")
            alert = (
                self.db.query(AiConfidenceAlert)
                .filter(AiConfidenceAlert.id == parsed_id, AiConfidenceAlert.case_id == case_id)
                .one_or_none()
            )
            if alert is not None:
                alert.resolved = True
                alert.resolved_by_admin_id = context.admin_user.id
                alert.resolved_at = utc_now()

    def _case_state(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "status": case.status,
            "statusReason": case.status_reason,
            "currentStep": case.current_step,
            "nextBestAction": case.next_best_action,
        }

    def _queue_state(self, item: CaseQueueItem) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "currentStage": item.current_stage,
            "adminStatus": item.admin_status,
            "priority": item.priority,
            "assignedToAdminId": str(item.assigned_to_admin_id) if item.assigned_to_admin_id else None,
            "assignedRole": item.assigned_role,
            "paymentStatus": item.payment_status,
            "documentStatus": item.document_status,
            "analysisStatus": item.analysis_status,
            "hasLowConfidenceAi": item.has_low_confidence_ai,
            "hasBlockingIssue": item.has_blocking_issue,
        }

    def _document_state(self, document: Document) -> dict[str, Any]:
        return {
            "id": str(document.id),
            "status": document.status,
            "validationStatus": document.validation_status,
            "updatedAt": document.updated_at,
        }

    def _note_payload(self, note: InternalNote) -> dict[str, Any]:
        return {
            "id": str(note.id),
            "caseId": str(note.case_id),
            "adminUserId": str(note.admin_user_id),
            "noteType": note.note_type,
            "visibility": note.visibility,
            "body": note.body,
            "relatedEntityType": note.related_entity_type,
            "relatedEntityId": str(note.related_entity_id) if note.related_entity_id else None,
            "createdAt": note.created_at,
            "updatedAt": note.updated_at,
        }

    def _alert_payload(self, alert: AiConfidenceAlert) -> dict[str, Any]:
        return {
            "id": str(alert.id),
            "caseId": str(alert.case_id),
            "source": alert.source,
            "severity": alert.severity,
            "confidenceScore": _decimal(alert.confidence_score),
            "title": alert.title,
            "description": alert.description,
            "recommendation": alert.recommendation,
            "resolved": alert.resolved,
            "resolvedByAdminId": str(alert.resolved_by_admin_id) if alert.resolved_by_admin_id else None,
            "resolvedAt": alert.resolved_at,
            "createdAt": alert.created_at,
        }

    def _review_task_payload(self, task: AdminReviewTask) -> dict[str, Any]:
        return {
            "id": str(task.id),
            "caseId": str(task.case_id),
            "taskType": task.task_type,
            "status": task.status,
            "assignedToAdminId": str(task.assigned_to_admin_id) if task.assigned_to_admin_id else None,
            "priority": task.priority,
            "title": task.title,
            "description": task.description,
            "blocking": task.blocking,
            "dueAt": task.due_at,
            "completedAt": task.completed_at,
            "createdAt": task.created_at,
            "updatedAt": task.updated_at,
        }

    def _admin_audit_payload(self, event: AdminAuditEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "actorAdminId": str(event.actor_admin_id),
            "caseId": str(event.case_id) if event.case_id else None,
            "eventType": event.event_type,
            "entityType": event.entity_type,
            "entityId": str(event.entity_id) if event.entity_id else None,
            "previousState": event.previous_state,
            "newState": event.new_state,
            "metadata": event.metadata_json,
            "createdAt": event.created_at,
        }

    def _case_history_payload(self, event: CaseHistoryEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "eventType": event.event_type,
            "title": event.title,
            "description": event.description,
            "visibility": event.visibility,
            "severity": event.severity,
            "createdAt": event.created_at,
        }

    def _extraction_field_payload(self, field: ExtractionField) -> dict[str, Any]:
        return {
            "id": str(field.id),
            "entityType": field.entity_type,
            "entityId": str(field.entity_id) if field.entity_id else None,
            "fieldKey": field.field_key,
            "rawValue": field.raw_value,
            "normalizedValue": field.normalized_value,
            "displayValue": field.display_value,
            "confidence": _decimal(field.confidence),
            "status": field.status,
            "sourceDocumentId": str(field.source_document_id) if field.source_document_id else None,
            "sourcePage": field.source_page,
            "sourceBbox": field.source_bbox,
            "sourceText": field.source_text,
            "extractionMethod": field.extraction_method,
            "needsReview": field.needs_review,
        }

    def _employer_payload(self, item: Employer) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "name": item.name,
            "rawName": item.raw_name,
            "nit": item.nit,
            "employerType": item.employer_type,
            "confidence": _decimal(item.confidence),
            "status": item.status,
            "source": item.source,
        }

    def _labor_period_payload(self, item: LaborPeriod) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "employerId": str(item.employer_id) if item.employer_id else None,
            "startDate": item.start_date,
            "endDate": item.end_date,
            "periodType": item.period_type,
            "regimeHint": item.regime_hint,
            "weeksDetected": _decimal(item.weeks_detected),
            "daysDetected": item.days_detected,
            "salaryBaseDetected": _decimal(item.salary_base_detected),
            "novelty": item.novelty,
            "confidence": _decimal(item.confidence),
            "status": item.status,
            "sourceDocumentId": str(item.source_document_id) if item.source_document_id else None,
            "sourcePage": item.source_page,
        }

    def _contribution_week_payload(self, item: ContributionWeek) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "year": item.year,
            "month": item.month,
            "weeks": _decimal(item.weeks),
            "days": item.days,
            "source": item.source,
            "confidence": _decimal(item.confidence),
            "status": item.status,
        }

    def _salary_base_payload(self, item: SalaryBase) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "periodYear": item.period_year,
            "periodMonth": item.period_month,
            "amount": _decimal(item.amount),
            "currency": item.currency,
            "rawValue": item.raw_value,
            "confidence": _decimal(item.confidence),
            "status": item.status,
        }

    def _novelty_payload(self, item: LaborNovelty) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "noveltyType": item.novelty_type,
            "description": item.description,
            "detectedBy": item.detected_by,
            "confidence": _decimal(item.confidence),
            "status": item.status,
        }

    def _gap_payload(self, item: ContributionGap) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "startDate": item.start_date,
            "endDate": item.end_date,
            "gapType": item.gap_type,
            "description": item.description,
            "severity": item.severity,
            "confidence": _decimal(item.confidence),
            "status": item.status,
        }

    def _extraction_issue_payload(self, item: ExtractionIssue) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "issueType": item.issue_type,
            "severity": item.severity,
            "message": item.message,
            "status": item.status,
            "resolutionNote": item.resolution_note,
            "resolvedAt": item.resolved_at,
        }

    def _case_owner_role(self, assignment_type: str) -> str:
        if assignment_type in {"legal_review", "legal_reviewer"}:
            return "legal_reviewer"
        return "admin_assignee"


def _parse_uuid(value: str | uuid.UUID, *, code: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND if code.endswith("NOT_FOUND") else status.HTTP_400_BAD_REQUEST,
            code=code,
            message="Identificador UUID invalido.",
        ) from exc


def _uuid_or_none(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value in {None, ""}:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _holder_full_name(case: LaboraCase) -> str:
    return f"{case.holder_first_name} {case.holder_last_name}".strip()


def _stage_for_case(case: LaboraCase) -> str:
    return case.current_step or case.status


def _admin_status_for_case(case: LaboraCase) -> str:
    if case.status == "requires_review":
        return "requires_review"
    if case.status == "blocked":
        return "blocked"
    if case.status in PAYMENT_CONFIRMED_STATUSES:
        return "payment_confirmed"
    if case.status == "completed":
        return "full_analysis_ready"
    return case.status


def _payment_status_for_case(case: LaboraCase) -> str:
    if case.status in PAYMENT_CONFIRMED_STATUSES:
        return "payment_confirmed"
    if case.status.startswith("payment_"):
        return case.status
    if case.status == "preview_locked":
        return "payment_pending"
    return "not_started"


def _analysis_status_for_case(case: LaboraCase) -> str:
    if case.status in {"analysis_in_progress", "full_analysis_unlocked"}:
        return "full_analysis_in_progress"
    if case.status == "completed":
        return "full_analysis_ready"
    if case.status in {"preanalysis_ready", "pre_analysis_ready"}:
        return "pre_analysis_ready"
    return "not_started"


def _aggregate_ocr_status(pages: list[DocumentPage]) -> str:
    if not pages:
        return "not_started"
    statuses = {page.ocr_status for page in pages}
    if "failed" in statuses:
        return "ocr_failed"
    if statuses <= {"completed"}:
        return "completed"
    if "processing" in statuses or "pending" in statuses:
        return "in_progress"
    return sorted(statuses)[0]


def _permission_for_alert_source(source: str) -> str | None:
    if source in {"document_ai", "ocr", "extraction"}:
        return "admin.documents.review"
    if source in {"calculation_engine"}:
        return "admin.calculations.review"
    return "admin.analysis.review"


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _decimal(value: Decimal | int | float | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
