import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.case import LaboraCase
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.schemas.case import (
    AdminAssignCaseRequest,
    AdminCaseTagRequest,
    CaseCloseRequest,
    CaseCreateRequest,
    CaseUpdateRequest,
    InternalAiSuggestionRequest,
    InternalCaseStatusUpdateRequest,
)
from app.services.consent_service import ConsentComplianceService
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
INTERNAL_ROLES = {"system", *ADMIN_ROLES}
LOCKED_STATUSES = {"closed", "archived"}
PAID_OR_LATER_STATUSES = {
    "paid_unlocked",
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
        "paid_unlocked",
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


class CaseService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.audit_events = AuditEventRepository(db)

    def create_case(
        self,
        payload: CaseCreateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_sensitive_consent(user.id)
        self._validate_holder_mode(
            holder_type=payload.holder_type,
            acting_as_third_party=payload.acting_as_third_party,
            third_party_relationship=payload.third_party_relationship,
        )

        for _attempt in range(3):
            try:
                now = utc_now()
                status_value = "created"
                current_step, next_best_action = step_for_status(status_value)
                case_number = self.cases.next_case_number(now.year)
                authorization_status = (
                    "pending"
                    if payload.acting_as_third_party
                    else "not_required"
                )
                case = self.cases.create(
                    case_number=case_number,
                    owner_user_id=user.id,
                    holder_type=payload.holder_type,
                    holder_first_name=payload.holder.first_name,
                    holder_last_name=payload.holder.last_name,
                    holder_document_type=payload.holder.document_type,
                    holder_document_number=payload.holder.document_number,
                    holder_birth_date=payload.holder.birth_date,
                    holder_email=payload.holder.email,
                    holder_phone=payload.holder.phone,
                    acting_as_third_party=payload.acting_as_third_party,
                    third_party_relationship=payload.third_party_relationship,
                    third_party_authorization_status=authorization_status,
                    case_type_requested=payload.case_type_requested,
                    pension_fund_or_entity=payload.pension_fund_or_entity,
                    situation_type=payload.situation_type,
                    status=status_value,
                    current_step=current_step,
                    next_best_action=next_best_action,
                    is_sensitive=True,
                    created_at=now,
                    updated_at=now,
                )
                owner_permissions = {
                    "edit_case": True,
                    "view_history": True,
                    "close_case": True,
                }
                self.cases.create_owner(
                    case_id=case.id,
                    user_id=user.id,
                    role="owner",
                    permissions=owner_permissions,
                )
                self.cases.create_owner(
                    case_id=case.id,
                    user_id=user.id,
                    role="creator",
                    permissions=owner_permissions,
                )
                self._record_status_history(
                    case=case,
                    previous_status=None,
                    reason="Case created.",
                    actor=user,
                    source_module="cases",
                    metadata=None,
                )
                self._record_history_event(
                    case=case,
                    event_type="expediente.created",
                    title="Expediente creado",
                    description=f"Se creo el expediente {case.case_number}.",
                    visibility="both",
                    severity="success",
                    actor=user,
                    metadata=None,
                )
                self._audit(
                    "expediente.created",
                    actor=user,
                    case=case,
                    new_state=self._audit_state(case),
                    source_module="cases",
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                self.db.commit()
                self.db.refresh(case)
                return self._created_response(case)
            except IntegrityError as exc:
                self.db.rollback()
                if _attempt == 2:
                    raise ApiError(
                        status_code=status.HTTP_409_CONFLICT,
                        code="DUPLICATE_CASE_NUMBER",
                        message="No fue posible generar un numero unico de expediente.",
                    ) from exc

        raise ApiError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="CASE_INTERNAL_ERROR",
            message="No fue posible crear el expediente.",
        )

    def list_my_cases(
        self,
        *,
        user: User,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        if status_filter and status_filter not in CASE_STATUSES:
            raise self._validation_error("Estado de expediente invalido.")
        cases, total = self.cases.list_user_cases(
            user_id=user.id,
            status=status_filter,
            page=page,
            page_size=page_size,
        )
        return {
            "data": [self._list_item(case) for case in cases],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "total": total,
            },
        }

    def get_case(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if not self._can_view(case, user):
            self._audit_access_denied(
                case=case,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self._audit(
            "expediente.viewed",
            actor=user,
            case=case,
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail(case, admin=False)

    def update_case(
        self,
        case_id: str,
        payload: CaseUpdateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        if case.status in LOCKED_STATUSES:
            raise self._locked_error()
        if (
            case.status in PAID_OR_LATER_STATUSES
            and payload.holder is not None
            and not self._is_admin(user)
        ):
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="CASE_LOCKED",
                message="No se puede cambiar el titular despues del desbloqueo de pago.",
            )

        previous_state = self._audit_state(case)
        changed = self._apply_update_payload(case, payload)
        if not changed:
            return self._update_response(case)

        case.updated_at = utc_now()
        self._record_history_event(
            case=case,
            event_type="expediente.updated",
            title="Expediente actualizado",
            description="Se actualizaron datos basicos del expediente.",
            visibility="both",
            severity="info",
            actor=user,
            metadata=None,
        )
        self._audit(
            "expediente.updated",
            actor=user,
            case=case,
            previous_state=previous_state,
            new_state=self._audit_state(case),
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(case)
        return self._update_response(case)

    def submit_case(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        if case.status in LOCKED_STATUSES:
            raise self._locked_error()
        if case.status not in {"draft", "created"}:
            raise self._state_conflict("El expediente no puede enviarse desde su estado actual.")

        previous_state = self._audit_state(case)
        previous_status = case.status
        self._transition_case(
            case,
            new_status="ready_for_documents",
            reason="User submitted case data.",
            actor=user,
            source_module="cases",
            metadata=None,
            validate_transition=True,
        )
        case.submitted_at = utc_now()
        self._record_history_event(
            case=case,
            event_type="expediente.submitted",
            title="Datos confirmados",
            description="El expediente quedo listo para cargar documentos.",
            visibility="both",
            severity="success",
            actor=user,
            metadata=None,
        )
        self._audit(
            "expediente.submitted",
            actor=user,
            case=case,
            previous_state=previous_state,
            new_state=self._audit_state(case),
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit_status_changed(
            actor=user,
            case=case,
            previous_status=previous_status,
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(case)
        return {
            "id": str(case.id),
            "status": case.status,
            "currentStep": case.current_step,
            "nextBestAction": case.next_best_action,
        }

    def get_history(
        self,
        case_id: str,
        *,
        user: User,
        sort: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if not self._can_view(case, user):
            self._audit_access_denied(
                case=case,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        include_internal = self._is_admin(user) or self._is_legal_reviewer(user)
        events = self.cases.list_history_events(
            case_id=case.id,
            include_internal=include_internal,
            sort=sort,
        )
        return {
            "data": [
                {
                    "id": str(event.id),
                    "eventType": event.event_type,
                    "title": event.title,
                    "description": event.description,
                    "severity": event.severity,
                    "createdAt": event.created_at,
                }
                for event in events
            ]
        }

    def close_case(
        self,
        case_id: str,
        payload: CaseCloseRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        if case.status == "archived":
            raise self._locked_error()
        if case.status == "closed":
            return {
                "id": str(case.id),
                "status": case.status,
                "closedAt": case.closed_at,
            }
        previous_status = case.status
        previous_state = self._audit_state(case)
        self._transition_case(
            case,
            new_status="closed",
            reason=payload.reason,
            actor=user,
            source_module="cases",
            metadata=None,
            validate_transition=True,
        )
        case.closed_at = utc_now()
        self._record_history_event(
            case=case,
            event_type="expediente.closed",
            title="Expediente cerrado",
            description=payload.reason,
            visibility="both",
            severity="warning",
            actor=user,
            metadata=None,
        )
        self._audit(
            "expediente.closed",
            actor=user,
            case=case,
            previous_state=previous_state,
            new_state=self._audit_state(case),
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit_status_changed(
            actor=user,
            case=case,
            previous_status=previous_status,
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(case)
        return {
            "id": str(case.id),
            "status": case.status,
            "closedAt": case.closed_at,
        }

    def update_status_internal(
        self,
        case_id: str,
        payload: InternalCaseStatusUpdateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_internal_role(user)
        case = self._get_case_or_404(case_id)
        previous_status = case.status
        previous_state = self._audit_state(case)
        self._transition_case(
            case,
            new_status=payload.new_status,
            reason=payload.reason,
            actor=user,
            source_module=payload.source_module,
            metadata=payload.metadata,
            validate_transition=True,
        )
        self._record_history_event(
            case=case,
            event_type="expediente.status_changed",
            title="Estado actualizado",
            description=payload.reason,
            visibility="both",
            severity=_severity_for_status(case.status),
            actor=user,
            metadata=payload.metadata,
        )
        self._audit_status_changed(
            actor=user,
            case=case,
            previous_status=previous_status,
            source_module=payload.source_module,
            ip_address=ip_address,
            user_agent=user_agent,
            previous_state=previous_state,
        )
        self.db.commit()
        self.db.refresh(case)
        return {
            "id": str(case.id),
            "previousStatus": previous_status,
            "newStatus": case.status,
        }

    def apply_ai_suggestion(
        self,
        case_id: str,
        payload: InternalAiSuggestionRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_internal_role(user)
        case = self._get_case_or_404(case_id)
        previous_state = self._audit_state(case)
        case.case_type_suggested = payload.case_type_suggested
        case.case_type_confidence = Decimal(str(payload.confidence))
        case.updated_at = utc_now()
        normalized_tags = [_normalize_tag(tag) for tag in payload.tags if _normalize_tag(tag)]
        for tag in normalized_tags:
            self.cases.add_tag(
                case_id=case.id,
                tag=tag,
                source="ai",
                confidence=payload.confidence,
            )
        self._record_history_event(
            case=case,
            event_type="expediente.updated",
            title="Sugerencia preliminar registrada",
            description="Se registro una sugerencia preliminar de tipo de caso.",
            visibility="internal",
            severity="info",
            actor=user,
            metadata={"source": payload.source, "tags": normalized_tags},
        )
        previous_status = case.status
        if payload.confidence < 0.6 and case.status != "requires_review":
            self._transition_case(
                case,
                new_status="requires_review",
                reason="Low confidence AI suggestion.",
                actor=user,
                source_module="preanalysis",
                metadata={"confidence": payload.confidence, "source": payload.source},
                validate_transition=True,
            )
        self._audit(
            "expediente.updated",
            actor=user,
            case=case,
            previous_state=previous_state,
            new_state=self._audit_state(case),
            source_module="preanalysis",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"aiSuggestionSource": payload.source},
        )
        if previous_status != case.status:
            self._audit_status_changed(
                actor=user,
                case=case,
                previous_status=previous_status,
                source_module="preanalysis",
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self.db.commit()
        self.db.refresh(case)
        return {
            "id": str(case.id),
            "caseTypeRequested": case.case_type_requested,
            "caseTypeSuggested": case.case_type_suggested,
            "caseTypeConfidence": float(case.case_type_confidence)
            if case.case_type_confidence is not None
            else None,
            "status": case.status,
            "tags": normalized_tags,
        }

    def list_admin_cases(
        self,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
        q: str | None,
        status_filter: str | None,
        case_type_requested: str | None,
        situation_type: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        updated_from: datetime | None,
        updated_to: datetime | None,
        assigned_to: str | None,
        tag: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        if not self._is_admin(user):
            raise self._access_denied()
        assigned_uuid = None
        if assigned_to:
            assigned_uuid = _parse_uuid_or_error(assigned_to)
        if status_filter and status_filter not in CASE_STATUSES:
            raise self._validation_error("Estado de expediente invalido.")
        cases, total = self.cases.list_admin_cases(
            q=q,
            status=status_filter,
            case_type_requested=case_type_requested,
            situation_type=situation_type,
            created_from=created_from,
            created_to=created_to,
            updated_from=updated_from,
            updated_to=updated_to,
            assigned_to=assigned_uuid,
            tag=_normalize_tag(tag) if tag else None,
            page=page,
            page_size=page_size,
        )
        response = {
            "data": [self._list_item(case) for case in cases],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "total": total,
            },
        }
        self.audit_events.create(
            event_type="expediente.admin_viewed",
            entity_type="case",
            actor_user_id=user.id,
            entity_id=None,
            metadata=_json_safe(
                {
                    "actorRole": self._actor_role(user),
                    "sourceModule": "cases",
                    "filters": {
                        "q": q,
                        "status": status_filter,
                        "caseTypeRequested": case_type_requested,
                        "situationType": situation_type,
                        "createdFrom": created_from,
                        "createdTo": created_to,
                        "updatedFrom": updated_from,
                        "updatedTo": updated_to,
                        "assignedTo": assigned_to,
                        "tag": tag,
                        "page": page,
                        "pageSize": page_size,
                    },
                }
            ),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return response

    def get_admin_case(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        if not self._can_admin_view(case, user):
            self._audit_access_denied(
                case=case,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self._audit(
            "expediente.admin_viewed",
            actor=user,
            case=case,
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail(case, admin=True)

    def assign_case(
        self,
        case_id: str,
        payload: AdminAssignCaseRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if not self._is_admin(user):
            raise self._access_denied()
        case = self._get_case_or_404(case_id)
        assignee_id = _parse_uuid_or_error(payload.assignee_user_id)
        assignee = self.db.get(User, assignee_id)
        if assignee is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="USER_NOT_FOUND",
                message="Usuario asignado no encontrado.",
            )
        owner = self.cases.create_owner(
            case_id=case.id,
            user_id=assignee_id,
            role=payload.role,
            permissions={"view_case": True, "view_history": True},
        )
        self._record_history_event(
            case=case,
            event_type="expediente.assigned",
            title="Responsable asignado",
            description="Se asigno un responsable interno.",
            visibility="internal",
            severity="info",
            actor=user,
            metadata={"assigneeUserId": str(assignee_id), "role": payload.role},
        )
        self._audit(
            "expediente.assigned",
            actor=user,
            case=case,
            new_state={"assigneeUserId": str(owner.user_id), "role": owner.role},
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "id": str(owner.id),
            "caseId": str(case.id),
            "assigneeUserId": str(owner.user_id),
            "role": owner.role,
        }

    def add_tag(
        self,
        case_id: str,
        payload: AdminCaseTagRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if not self._is_admin(user):
            raise self._access_denied()
        case = self._get_case_or_404(case_id)
        tag = self.cases.add_tag(
            case_id=case.id,
            tag=payload.tag,
            source=payload.source,
            confidence=None,
        )
        self._record_history_event(
            case=case,
            event_type="expediente.tag_added",
            title="Etiqueta agregada",
            description=f"Se agrego la etiqueta {tag.tag}.",
            visibility="internal",
            severity="info",
            actor=user,
            metadata={"tag": tag.tag, "source": tag.source},
        )
        self._audit(
            "expediente.tag_added",
            actor=user,
            case=case,
            new_state={"tag": tag.tag, "source": tag.source},
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "id": str(tag.id),
            "caseId": str(case.id),
            "tag": tag.tag,
            "source": tag.source,
        }

    def _apply_update_payload(self, case: LaboraCase, payload: CaseUpdateRequest) -> bool:
        changed = False
        holder_data = (
            payload.holder.model_dump(exclude_unset=True)
            if payload.holder is not None
            else {}
        )
        holder_mapping = {
            "first_name": "holder_first_name",
            "last_name": "holder_last_name",
            "document_type": "holder_document_type",
            "document_number": "holder_document_number",
            "birth_date": "holder_birth_date",
            "email": "holder_email",
            "phone": "holder_phone",
        }
        for source, target in holder_mapping.items():
            if source in holder_data:
                changed = self._set_if_changed(case, target, holder_data[source]) or changed

        payload_data = payload.model_dump(exclude_unset=True, exclude={"holder"})
        if {
            "holder_type",
            "acting_as_third_party",
            "third_party_relationship",
        } & payload_data.keys():
            next_holder_type = payload_data.get("holder_type", case.holder_type)
            next_acting = payload_data.get(
                "acting_as_third_party",
                case.acting_as_third_party,
            )
            next_relationship = payload_data.get(
                "third_party_relationship",
                case.third_party_relationship,
            )
            if not next_acting and "third_party_relationship" not in payload_data:
                next_relationship = None
            self._validate_holder_mode(
                holder_type=next_holder_type,
                acting_as_third_party=next_acting,
                third_party_relationship=next_relationship,
            )

        scalar_mapping = {
            "holder_type": "holder_type",
            "case_type_requested": "case_type_requested",
            "pension_fund_or_entity": "pension_fund_or_entity",
            "situation_type": "situation_type",
        }
        for source, target in scalar_mapping.items():
            if source in payload_data:
                changed = self._set_if_changed(case, target, payload_data[source]) or changed

        if "acting_as_third_party" in payload_data:
            acting = payload_data["acting_as_third_party"]
            changed = self._set_if_changed(case, "acting_as_third_party", acting) or changed
            if acting:
                changed = self._set_if_changed(
                    case,
                    "third_party_authorization_status",
                    "pending",
                ) or changed
            else:
                changed = self._set_if_changed(
                    case,
                    "third_party_authorization_status",
                    "not_required",
                ) or changed
                changed = self._set_if_changed(
                    case,
                    "third_party_relationship",
                    None,
                ) or changed

        if "third_party_relationship" in payload_data:
            relationship = payload_data["third_party_relationship"]
            changed = self._set_if_changed(
                case,
                "third_party_relationship",
                relationship,
            ) or changed

        return changed

    def _transition_case(
        self,
        case: LaboraCase,
        *,
        new_status: str,
        reason: str | None,
        actor: User,
        source_module: str,
        metadata: dict[str, Any] | None,
        validate_transition: bool,
    ) -> None:
        if new_status not in CASE_STATUSES:
            raise self._validation_error("Estado de expediente invalido.")
        previous_status = case.status
        if (
            validate_transition
            and new_status != previous_status
            and new_status not in CASE_STATUS_TRANSITIONS.get(previous_status, set())
        ):
            raise self._state_conflict(
                f"Transicion invalida de {previous_status} a {new_status}.",
            )
        current_step, next_best_action = step_for_status(new_status)
        case.status = new_status
        case.status_reason = reason
        case.current_step = current_step
        case.next_best_action = next_best_action
        case.updated_at = utc_now()
        if new_status != previous_status:
            self._record_status_history(
                case=case,
                previous_status=previous_status,
                reason=reason,
                actor=actor,
                source_module=source_module,
                metadata=metadata,
            )

    def _record_status_history(
        self,
        *,
        case: LaboraCase,
        previous_status: str | None,
        reason: str | None,
        actor: User,
        source_module: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.cases.create_status_history(
            case_id=case.id,
            previous_status=previous_status,
            new_status=case.status,
            reason=reason,
            changed_by_user_id=actor.id,
            changed_by_role=self._actor_role(actor),
            source_module=source_module,
            metadata=_json_safe(metadata) if metadata else None,
        )

    def _record_history_event(
        self,
        *,
        case: LaboraCase,
        event_type: str,
        title: str,
        description: str | None,
        visibility: str,
        severity: str,
        actor: User,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.cases.create_history_event(
            case_id=case.id,
            event_type=event_type,
            title=title,
            description=description,
            visibility=visibility,
            severity=severity,
            created_by_user_id=actor.id,
            metadata=_json_safe(metadata) if metadata else None,
        )

    def _audit_status_changed(
        self,
        *,
        actor: User,
        case: LaboraCase,
        previous_status: str | None,
        source_module: str,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
    ) -> None:
        self._audit(
            "expediente.status_changed",
            actor=actor,
            case=case,
            previous_state=previous_state or {"status": previous_status},
            new_state={"status": case.status},
            source_module=source_module,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _audit(
        self,
        event_type: str,
        *,
        actor: User,
        case: LaboraCase,
        source_module: str,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        audit_metadata = {
            "actorRole": self._actor_role(actor),
            "sourceModule": source_module,
        }
        if metadata:
            audit_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_type,
            entity_type="case",
            actor_user_id=actor.id,
            entity_id=case.id,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata=_json_safe(audit_metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _audit_access_denied(
        self,
        *,
        case: LaboraCase,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._audit(
            "expediente.access_denied",
            actor=actor,
            case=case,
            source_module="cases",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise self._access_denied()

    def _require_sensitive_consent(self, user_id: uuid.UUID) -> None:
        permission = ConsentComplianceService(self.db).can_upload_documents(user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=422,
                code="CONSENT_REQUIRED",
                message="Debes aceptar los consentimientos requeridos antes de crear el expediente.",
                details={
                    "missingConsentTypes": permission.missing_consent_types,
                    "reason": permission.reason,
                },
            )

    def _require_can_update(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if not self._can_update(case, user):
            self._audit_access_denied(
                case=case,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    def _require_internal_role(self, user: User) -> None:
        if user.role not in INTERNAL_ROLES:
            raise self._access_denied()

    def _get_case_or_404(self, case_id: str) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _validate_holder_mode(
        self,
        *,
        holder_type: str,
        acting_as_third_party: bool,
        third_party_relationship: str | None,
    ) -> None:
        if acting_as_third_party and holder_type != "third_party":
            raise self._validation_error("holderType debe ser third_party.")
        if not acting_as_third_party and holder_type != "self":
            raise self._validation_error("holderType debe ser self.")
        if acting_as_third_party and not third_party_relationship:
            raise self._validation_error("thirdPartyRelationship es obligatorio.")
        if not acting_as_third_party and third_party_relationship is not None:
            raise self._validation_error("thirdPartyRelationship debe ser null.")

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if self._is_legal_reviewer(user):
            return self._can_admin_view(case, user)
        if case.owner_user_id == user.id:
            return True
        return (
            self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"authorized_user", "creator", "owner"},
            )
            is not None
        )

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _can_admin_view(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if self._is_legal_reviewer(user):
            return (
                case.status == "requires_review"
                or self.cases.get_owner(
                    case_id=case.id,
                    user_id=user.id,
                    roles={"legal_reviewer"},
                )
                is not None
            )
        return False

    def _detail(self, case: LaboraCase, *, admin: bool) -> dict[str, Any]:
        current_step, next_best_action = serialized_step_for_case(case)
        detail = {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "holderType": case.holder_type,
            "holder": {
                "firstName": case.holder_first_name,
                "lastName": case.holder_last_name,
                "documentType": case.holder_document_type,
                "documentNumberMasked": _mask_document(case.holder_document_number),
                "birthDate": case.holder_birth_date,
                "emailMasked": _mask_email(case.holder_email),
                "phoneMasked": _mask_phone(case.holder_phone),
            },
            "actingAsThirdParty": case.acting_as_third_party,
            "thirdPartyRelationship": case.third_party_relationship,
            "thirdPartyAuthorizationStatus": case.third_party_authorization_status,
            "caseTypeRequested": case.case_type_requested,
            "caseTypeSuggested": case.case_type_suggested,
            "caseTypeConfidence": float(case.case_type_confidence)
            if case.case_type_confidence is not None
            else None,
            "pensionFundOrEntity": case.pension_fund_or_entity,
            "situationType": case.situation_type,
            "status": case.status,
            "statusReason": case.status_reason,
            "currentStep": current_step,
            "nextBestAction": next_best_action,
            "allowedActions": allowed_actions_for_status(case.status),
            "createdAt": case.created_at,
            "updatedAt": case.updated_at,
        }
        if admin:
            detail.update(
                {
                    "ownerUserId": str(case.owner_user_id),
                    "holderDocumentNumber": case.holder_document_number,
                    "holderEmail": case.holder_email,
                    "holderPhone": case.holder_phone,
                    "tags": [tag.tag for tag in self.cases.list_tags(case.id)],
                }
            )
        return detail

    def _list_item(self, case: LaboraCase) -> dict[str, Any]:
        current_step, next_best_action = serialized_step_for_case(case)
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "holderFullName": _holder_full_name(case),
            "caseTypeRequested": case.case_type_requested,
            "status": case.status,
            "currentStep": current_step,
            "nextBestAction": next_best_action,
            "allowedActions": allowed_actions_for_status(case.status),
            "updatedAt": case.updated_at,
        }

    def _created_response(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "status": case.status,
            "currentStep": case.current_step,
            "nextBestAction": case.next_best_action,
            "createdAt": case.created_at,
        }

    def _update_response(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "status": case.status,
            "updatedAt": case.updated_at,
        }

    def _audit_state(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "ownerUserId": str(case.owner_user_id),
            "holderType": case.holder_type,
            "holderFirstName": case.holder_first_name,
            "holderLastName": case.holder_last_name,
            "holderDocumentType": case.holder_document_type,
            "holderDocumentNumber": case.holder_document_number,
            "actingAsThirdParty": case.acting_as_third_party,
            "thirdPartyRelationship": case.third_party_relationship,
            "thirdPartyAuthorizationStatus": case.third_party_authorization_status,
            "caseTypeRequested": case.case_type_requested,
            "caseTypeSuggested": case.case_type_suggested,
            "caseTypeConfidence": case.case_type_confidence,
            "pensionFundOrEntity": case.pension_fund_or_entity,
            "situationType": case.situation_type,
            "status": case.status,
            "statusReason": case.status_reason,
            "currentStep": case.current_step,
            "nextBestAction": case.next_best_action,
        }

    def _set_if_changed(self, case: LaboraCase, attribute: str, value: Any) -> bool:
        if getattr(case, attribute) == value:
            return False
        setattr(case, attribute, value)
        return True

    def _actor_role(self, user: User) -> str:
        if self._is_admin(user):
            return "admin"
        if self._is_legal_reviewer(user):
            return "legal_reviewer"
        if user.role == "system":
            return "system"
        return "user"

    def _is_admin(self, user: User) -> bool:
        return user.role in ADMIN_ROLES

    def _is_legal_reviewer(self, user: User) -> bool:
        return user.role in LEGAL_REVIEWER_ROLES

    def _access_denied(self) -> ApiError:
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CASE_ACCESS_DENIED",
            message="No tienes permisos para acceder a este expediente.",
        )

    def _locked_error(self) -> ApiError:
        return ApiError(
            status_code=status.HTTP_423_LOCKED,
            code="CASE_LOCKED",
            message="El expediente esta cerrado o archivado.",
        )

    def _state_conflict(self, message: str) -> ApiError:
        return ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="CASE_STATE_CONFLICT",
            message=message,
        )

    def _validation_error(self, message: str) -> ApiError:
        return ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="CASE_VALIDATION_ERROR",
            message=message,
        )


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
        "paid_unlocked": ("analysis_unlocked", "start_full_analysis"),
        "analysis_in_progress": ("analysis_in_progress", "wait_analysis"),
        "completed": ("completed", "view_report"),
        "requires_review": ("requires_review", "request_professional_review"),
        "blocked": ("blocked", "contact_support"),
        "closed": ("closed", "view_history"),
        "archived": ("archived", "view_history"),
        "error": ("error", "contact_support"),
    }
    return mapping[status_value]


def serialized_step_for_case(case: LaboraCase) -> tuple[str, str]:
    current_step, next_best_action = step_for_status(case.status)
    if case.status == "documents_uploaded" and case.current_step == "preanalysis_pending":
        current_step = "preanalysis_pending"
    return current_step, next_best_action


def allowed_actions_for_status(status_value: str) -> list[str]:
    actions = ["view_history"]
    if status_value in {"draft", "created"}:
        actions.extend(["edit_case", "submit_case", "upload_documents", "close_case"])
    elif status_value in {"ready_for_documents", "documents_pending"}:
        actions.extend(["edit_case", "upload_documents", "close_case"])
    elif status_value in {"documents_uploaded", "preanalysis_pending"}:
        actions.extend(["view_documents", "start_preanalysis", "close_case"])
    elif status_value == "preanalysis_ready":
        actions.extend(["view_documents", "view_preanalysis", "close_case"])
    elif status_value == "preview_locked":
        actions.extend(["view_preanalysis", "unlock_full_analysis", "close_case"])
    elif status_value == "paid_unlocked":
        actions.extend(["view_preanalysis", "close_case"])
    elif status_value == "analysis_in_progress":
        actions.extend(["view_preanalysis"])
    elif status_value == "completed":
        actions.extend(["view_report", "generate_legal_action"])
    elif status_value == "requires_review":
        actions.extend(["request_professional_review", "close_case"])
    elif status_value in {"blocked", "error"}:
        actions.extend(["request_professional_review", "close_case"])
    return actions


def _severity_for_status(status_value: str) -> str:
    if status_value == "error":
        return "error"
    if status_value in {"blocked", "requires_review", "closed"}:
        return "warning"
    if status_value in {"completed", "paid_unlocked", "documents_uploaded"}:
        return "success"
    return "info"


def _holder_full_name(case: LaboraCase) -> str:
    return f"{case.holder_first_name} {case.holder_last_name}".strip()


def _mask_document(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * max(len(value) - 4, 0)}{value[-2:]}"


def _mask_email(value: str | None) -> str | None:
    if not value:
        return None
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def _mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}{'*' * max(len(value) - 7, 0)}{value[-4:]}"


def _normalize_tag(value: str) -> str:
    tag = re.sub(r"[^a-z0-9_:-]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", tag).strip("_")


def _parse_uuid_or_error(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="CASE_VALIDATION_ERROR",
            message="Identificador UUID invalido.",
        ) from exc


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
