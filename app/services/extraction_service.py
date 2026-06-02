import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.document import Document
from app.models.extraction import (
    ContributionGap,
    ContributionWeek,
    Employer,
    ExtractionField,
    ExtractionIssue,
    ExtractionJob,
    ExtractionRun,
    LaborNovelty,
    LaborPeriod,
    SalaryBase,
)
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.extraction_repository import ExtractionRepository
from app.schemas.extraction import (
    ConfirmExtractionRequest,
    ExtractionFieldsPatchRequest,
    ExtractionRunCreateRequest,
    IgnoreEntityRequest,
    IssueResolveRequest,
    ManualEmployerCreateRequest,
    ManualLaborPeriodCreateRequest,
)
from app.services.case_state_machine import step_for_status, validate_case_transition
from app.services.consent_service import ConsentComplianceService
from app.services.extraction_ai_provider import (
    AiExtractionProviderError,
    extraction_provider_factory,
)
from app.services.extraction_normalization import (
    ExtractionNormalizationError,
    is_blocking_confidence,
    needs_review_for_confidence,
    normalize_date_value,
    normalize_decimal_value,
    normalize_employer_name,
    normalize_money_value,
    short_source_text,
    status_for_confidence,
    validate_labor_period_range,
)
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
INTERNAL_ROLES = {"system", *ADMIN_ROLES}
LOCKED_STATUSES = {"closed", "archived"}
READY_DOCUMENT_STATUSES = {"uploaded", "validated", "requires_review"}
WRITABLE_FIELD_STATUSES = {
    "extracted",
    "normalized",
    "low_confidence",
    "corrected_by_user",
    "pending_user_confirmation",
    "conflict",
}
ENTITY_MODELS = {
    "extraction_field": ExtractionField,
    "field": ExtractionField,
    "employer": Employer,
    "labor_period": LaborPeriod,
    "contribution_week": ContributionWeek,
    "salary_base": SalaryBase,
    "contribution_gap": ContributionGap,
    "labor_novelty": LaborNovelty,
}
FIELD_ALIASES = {
    "startDate": "start_date",
    "endDate": "end_date",
    "weeksDetected": "weeks_detected",
    "daysDetected": "days_detected",
    "salaryBaseDetected": "salary_base_detected",
    "periodType": "period_type",
    "regimeHint": "regime_hint",
    "employerName": "name",
    "employerType": "employer_type",
}


class ExtractionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.documents = DocumentRepository(db)
        self.extractions = ExtractionRepository(db)
        self.audit_events = AuditEventRepository(db)

    def get_extraction(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        run = self.extractions.latest_run(case.id)
        if run is None:
            document = self._primary_labor_history_document(case)
            if document is not None:
                auto_result = self.start_initial_run_for_document(
                    str(document.id),
                    user=user,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                if auto_result is not None:
                    run = self.extractions.get_run(auto_result["extractionRunId"])
        self._audit(
            "extraccion_validacion_datos.viewed",
            actor=user,
            case=case,
            entity_type="extraction_run" if run else "case",
            entity_id=run.id if run else case.id,
            metadata={"hasExtractionRun": run is not None},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._extraction_payload(case=case, run=run, user=user)

    def start_run(
        self,
        case_id: str,
        payload: ExtractionRunCreateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_start(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        self._require_sensitive_consent(user)
        if self.extractions.running_run(case.id) is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="EXTRACTION_ALREADY_RUNNING",
                message="Ya hay una extraccion en progreso para este expediente.",
            )
        if payload.mode == "reprocess" and not settings.extraction_enable_reprocess:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="EXTRACTION_REPROCESS_DISABLED",
                message="El reprocesamiento de extraccion esta deshabilitado.",
            )
        documents = self._validated_documents(case=case, document_ids=payload.document_ids)
        questionnaire_id = _parse_uuid(payload.questionnaire_response_id)
        if payload.questionnaire_response_id and questionnaire_id is None:
            raise self._invalid_value("questionnaireResponseId debe ser UUID.")
        if not documents and questionnaire_id is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENTS_NOT_READY",
                message="No hay documentos aptos ni respuestas suficientes para iniciar extraccion.",
            )

        existing_run = self._find_reusable_run(
            case_id=case.id,
            document_ids=[str(document.id) for document in documents],
            questionnaire_id=questionnaire_id,
            reusable_statuses={"in_progress", "completed", "requires_review", "error"},
        )
        if existing_run is not None:
            job = self.extractions.latest_job_for_run(existing_run.id)
            return {
                "extractionRunId": str(existing_run.id),
                "status": existing_run.status,
                "jobId": str(job.id) if job else "",
            }

        now = utc_now()
        provider = extraction_provider_factory(payload.ai_provider)
        run = self.extractions.create_run(
            case_id=case.id,
            status="in_progress",
            confirmation_status="draft",
            source="manual" if payload.ai_provider == "none" else "mixed",
            ai_provider=provider.provider_name,
            ai_model=provider.model,
            document_ids=[str(document.id) for document in documents],
            questionnaire_response_id=questionnaire_id,
            low_confidence_count=0,
            issues_count=0,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        job = self.extractions.create_job(
            case_id=case.id,
            extraction_run_id=run.id,
            job_type=_job_type_for_mode(payload.mode),
            status="queued",
            progress=Decimal("0.00"),
            logs=[],
            retry_count=0,
            max_retries=settings.extraction_job_max_retries,
            idempotency_key=self._idempotency_key(
                case_id=case.id,
                document_ids=run.document_ids,
                questionnaire_id=questionnaire_id,
                mode=payload.mode,
            ),
            created_at=now,
            updated_at=now,
        )
        self._audit(
            "extraction.run.started",
            actor=user,
            case=case,
            entity_type="extraction_run",
            entity_id=run.id,
            new_state=self._run_state(run),
            metadata={"jobId": str(job.id), "mode": payload.mode},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._record_history_event(
            case=case,
            event_type="extraccion_validacion_datos.created",
            title="Extraccion iniciada",
            description="Se inicio la extraccion y validacion de datos laborales.",
            severity="info",
            actor=user,
            metadata={"extractionRunId": str(run.id), "jobId": str(job.id)},
        )

        self._run_job(
            case=case,
            run=run,
            job=job,
            documents=documents,
            questionnaire_id=questionnaire_id,
            provider_name=payload.ai_provider,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(run)
        return {
            "extractionRunId": str(run.id),
            "status": run.status,
            "jobId": str(job.id),
        }

    def start_initial_run_for_document(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any] | None:
        document = self.documents.get(document_id)
        if document is None or not self._is_labor_history_document(document):
            return None
        case = self._get_case_or_404(document.case_id)
        self._require_can_start(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        self._require_sensitive_consent(user)
        if document.deleted_at is not None or document.status not in READY_DOCUMENT_STATUSES:
            return None

        document_ids = [str(document.id)]
        existing_run = self._find_reusable_run(
            case_id=case.id,
            document_ids=document_ids,
            questionnaire_id=None,
            reusable_statuses={"in_progress", "completed", "requires_review", "error"},
        )
        if existing_run is not None:
            job = self.extractions.latest_job_for_run(existing_run.id)
            return {
                "extractionRunId": str(existing_run.id),
                "status": existing_run.status,
                "jobId": str(job.id) if job else None,
                "reused": True,
            }

        running_run = self.extractions.running_run(case.id)
        if running_run is not None:
            job = self.extractions.latest_job_for_run(running_run.id)
            return {
                "extractionRunId": str(running_run.id),
                "status": running_run.status,
                "jobId": str(job.id) if job else None,
                "reused": True,
            }

        now = utc_now()
        provider = extraction_provider_factory(settings.ai_extraction_provider)
        run = self.extractions.create_run(
            case_id=case.id,
            status="in_progress",
            confirmation_status="draft",
            source="manual" if provider.provider_name == "none" else "mixed",
            ai_provider=provider.provider_name,
            ai_model=provider.model,
            document_ids=document_ids,
            questionnaire_response_id=None,
            low_confidence_count=0,
            issues_count=0,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        job = self.extractions.create_job(
            case_id=case.id,
            extraction_run_id=run.id,
            job_type=_job_type_for_mode("initial"),
            status="queued",
            progress=Decimal("0.00"),
            logs=[],
            retry_count=0,
            max_retries=settings.extraction_job_max_retries,
            idempotency_key=self._idempotency_key(
                case_id=case.id,
                document_ids=run.document_ids,
                questionnaire_id=None,
                mode="initial",
            ),
            created_at=now,
            updated_at=now,
        )
        self._audit(
            "extraction.run.started",
            actor=user,
            case=case,
            entity_type="extraction_run",
            entity_id=run.id,
            new_state=self._run_state(run),
            metadata={"jobId": str(job.id), "mode": "initial", "trigger": "document_upload"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._record_history_event(
            case=case,
            event_type="extraccion_validacion_datos.created",
            title="Extraccion iniciada",
            description="Se inicio la extraccion automatica desde la historia laboral cargada.",
            severity="info",
            actor=user,
            metadata={"extractionRunId": str(run.id), "jobId": str(job.id), "documentId": str(document.id)},
        )
        try:
            self._run_job(
                case=case,
                run=run,
                job=job,
                documents=[document],
                questionnaire_id=None,
                provider_name=settings.ai_extraction_provider,
                actor=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except ApiError:
            pass
        return {
            "extractionRunId": str(run.id),
            "status": run.status,
            "jobId": str(job.id),
            "reused": False,
        }

    def update_fields(
        self,
        case_id: str,
        payload: ExtractionFieldsPatchRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        corrections: list[dict[str, str]] = []
        for item in payload.updates:
            field = self.extractions.get_field(item.field_id)
            if field is None or field.case_id != case.id:
                raise ApiError(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code="EXTRACTION_FIELD_NOT_FOUND",
                    message="Campo extraido no encontrado.",
                )
            if field.status not in WRITABLE_FIELD_STATUSES:
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="INVALID_FIELD_VALUE",
                    message="El campo no puede corregirse en su estado actual.",
                )
            previous_state = self._field_state(field)
            normalized_value, display_value = self._normalize_field_value(
                entity_type=item.entity_type or field.entity_type,
                field_key=item.field_key,
                value=item.new_value,
            )
            previous_value = field.normalized_value if field.normalized_value is not None else field.display_value
            field.field_key = _canonical_field_key(item.field_key)
            field.normalized_value = normalized_value
            field.display_value = display_value
            field.status = "corrected_by_user" if not self._is_admin(user) else "corrected_by_admin"
            field.needs_review = False
            field.updated_at = utc_now()
            self._apply_entity_update(field, normalized_value)
            correction = self.extractions.create_correction(
                case_id=case.id,
                extraction_run_id=field.extraction_run_id,
                extraction_field_id=field.id,
                entity_type=field.entity_type,
                entity_id=field.entity_id,
                field_key=field.field_key,
                previous_value=_json_safe(previous_value),
                new_value=_json_safe(normalized_value),
                reason=item.reason,
                corrected_by_user_id=user.id,
                correction_source="admin" if self._is_admin(user) else "user",
            )
            self._audit(
                "extraction.field.corrected",
                actor=user,
                case=case,
                entity_type=field.entity_type,
                entity_id=field.entity_id or field.id,
                previous_state=previous_state,
                new_state=self._field_state(field),
                metadata={"correctionId": str(correction.id), "reason": item.reason},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            corrections.append(
                {
                    "correctionId": str(correction.id),
                    "fieldId": str(field.id),
                    "status": field.status,
                }
            )
        run = self.extractions.latest_run(case.id)
        if run is not None:
            run.confirmation_status = "user_reviewing"
            self._recalculate_run(run)
        self.db.commit()
        return {"updated": len(corrections), "corrections": corrections}

    def create_employer(
        self,
        case_id: str,
        payload: ManualEmployerCreateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        run = self._get_or_create_manual_run(case=case, user=user, ip_address=ip_address, user_agent=user_agent)
        normalized_name = normalize_employer_name(payload.name)
        existing = self.extractions.find_employer(
            case_id=case.id,
            name=normalized_name,
            nit=payload.nit,
        )
        if existing is not None:
            employer = existing
            previous_state = self._employer_state(employer)
            employer.nit = payload.nit or employer.nit
            employer.employer_type = payload.employer_type or employer.employer_type
            employer.status = "corrected_by_user"
            employer.source = "manual"
            employer.updated_at = utc_now()
        else:
            previous_state = None
            employer = self.extractions.create_employer(
                case_id=case.id,
                extraction_run_id=run.id,
                name=normalized_name,
                raw_name=payload.name,
                nit=payload.nit,
                employer_type=payload.employer_type,
                confidence=Decimal("1.0000"),
                status="corrected_by_user",
                source="manual",
            )
        correction = self.extractions.create_correction(
            case_id=case.id,
            extraction_run_id=run.id,
            extraction_field_id=None,
            entity_type="employer",
            entity_id=employer.id,
            field_key="__entity__",
            previous_value=_json_safe(previous_state),
            new_value=self._employer_state(employer),
            reason=payload.reason,
            corrected_by_user_id=user.id,
            correction_source="user",
        )
        run.confirmation_status = "user_reviewing"
        run.status = "completed"
        self._recalculate_run(run)
        self._audit(
            "extraccion_validacion_datos.updated",
            actor=user,
            case=case,
            entity_type="employer",
            entity_id=employer.id,
            previous_state=previous_state,
            new_state=self._employer_state(employer),
            metadata={"correctionId": str(correction.id), "reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._employer_response(employer)

    def create_labor_period(
        self,
        case_id: str,
        payload: ManualLaborPeriodCreateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        run = self._get_or_create_manual_run(case=case, user=user, ip_address=ip_address, user_agent=user_agent)
        employer = None
        employer_id = _parse_uuid(payload.employer_id)
        if payload.employer_id and employer_id is None:
            raise self._invalid_value("employerId debe ser UUID.")
        if employer_id is not None:
            employer = self.db.get(Employer, employer_id)
            if employer is None or employer.case_id != case.id or employer.status == "ignored":
                raise self._invalid_value("El empleador no existe para este expediente.")
        validate_labor_period_range(payload.start_date, payload.end_date)
        period = self.extractions.create_labor_period(
            case_id=case.id,
            extraction_run_id=run.id,
            employer_id=employer.id if employer else None,
            start_date=payload.start_date,
            end_date=payload.end_date,
            period_type=payload.period_type,
            regime_hint=payload.regime_hint,
            weeks_detected=Decimal(str(payload.weeks_detected))
            if payload.weeks_detected is not None
            else None,
            days_detected=None,
            salary_base_detected=None,
            novelty=None,
            confidence=Decimal("1.0000"),
            status="corrected_by_user",
            source="manual",
            source_document_id=None,
            source_page=None,
        )
        overlaps = self.extractions.overlapping_periods(
            case_id=case.id,
            start_date=period.start_date,
            end_date=period.end_date,
            exclude_period_id=period.id,
        )
        if overlaps:
            issue = self.extractions.create_issue(
                case_id=case.id,
                extraction_run_id=run.id,
                extraction_field_id=None,
                entity_type="labor_period",
                entity_id=period.id,
                issue_type="overlap",
                severity="medium",
                message="El periodo laboral se solapa con otro periodo del expediente.",
                status="open",
            )
            self._audit(
                "extraction.issue.created",
                actor=user,
                case=case,
                entity_type="labor_period",
                entity_id=period.id,
                new_state=self._issue_state(issue),
                metadata={"overlappingPeriodIds": [str(item.id) for item in overlaps]},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        correction = self.extractions.create_correction(
            case_id=case.id,
            extraction_run_id=run.id,
            extraction_field_id=None,
            entity_type="labor_period",
            entity_id=period.id,
            field_key="__entity__",
            previous_value=None,
            new_value=self._period_state(period),
            reason=payload.reason,
            corrected_by_user_id=user.id,
            correction_source="user",
        )
        run.confirmation_status = "user_reviewing"
        run.status = "requires_review" if overlaps else "completed"
        self._recalculate_run(run)
        self._audit(
            "extraccion_validacion_datos.updated",
            actor=user,
            case=case,
            entity_type="labor_period",
            entity_id=period.id,
            new_state=self._period_state(period),
            metadata={"correctionId": str(correction.id), "reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._labor_period_response(period, {str(employer.id): employer} if employer else {})

    def ignore_entity(
        self,
        case_id: str,
        entity_type: str,
        entity_id: str,
        payload: IgnoreEntityRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_case_allows_writes(case, user)
        entity_type = _canonical_entity_type(entity_type)
        entity = self._get_entity_or_404(entity_type, entity_id, case_id=case.id)
        previous_state = self._entity_state(entity)
        entity.status = "ignored"
        if hasattr(entity, "needs_review"):
            entity.needs_review = False
        if hasattr(entity, "updated_at"):
            entity.updated_at = utc_now()
        run = self.extractions.latest_run(case.id)
        correction = self.extractions.create_correction(
            case_id=case.id,
            extraction_run_id=run.id if run else None,
            extraction_field_id=entity.id if isinstance(entity, ExtractionField) else None,
            entity_type=entity_type,
            entity_id=entity.id,
            field_key="__entity__",
            previous_value=previous_state,
            new_value=self._entity_state(entity),
            reason=payload.reason,
            corrected_by_user_id=user.id,
            correction_source="admin" if self._is_admin(user) else "user",
        )
        if run is not None:
            run.confirmation_status = "user_reviewing"
            self._recalculate_run(run)
        self._audit(
            "extraction.field.ignored" if isinstance(entity, ExtractionField) else "extraccion_validacion_datos.updated",
            actor=user,
            case=case,
            entity_type=entity_type,
            entity_id=entity.id,
            previous_state=previous_state,
            new_state=self._entity_state(entity),
            metadata={"correctionId": str(correction.id), "reason": payload.reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"entityType": entity_type, "entityId": str(entity.id), "status": "ignored"}

    def confirm_extraction(
        self,
        case_id: str,
        payload: ConfirmExtractionRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_confirm(case, user, ip_address, user_agent)
        run = self.extractions.latest_run(case.id)
        if run is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="EXTRACTION_NOT_FOUND",
                message="No existe extraccion para confirmar.",
            )
        low_fields = self._low_confidence_fields(case.id)
        if low_fields and not payload.accept_low_confidence_fields and not payload.mark_pending_fields:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="LOW_CONFIDENCE_FIELDS_PENDING",
                message="Hay campos de baja confianza pendientes por revisar.",
                details={"lowConfidenceCount": len(low_fields)},
            )
        previous_state = self._run_state(run)
        pending_count = 0
        if payload.mark_pending_fields and not payload.accept_low_confidence_fields:
            pending_count = len(low_fields)
            for field in low_fields:
                field.status = "pending_user_confirmation"
                field.needs_review = False
                field.updated_at = utc_now()
        elif payload.accept_low_confidence_fields:
            for field in low_fields:
                field.status = "confirmed"
                field.needs_review = False
                field.updated_at = utc_now()
        confirmation_status = (
            "confirmed_with_pending_fields"
            if pending_count > 0
            else "user_confirmed"
        )
        now = utc_now()
        run.confirmation_status = confirmation_status
        run.status = "completed"
        run.completed_at = now
        run.updated_at = now
        self._recalculate_run(run, keep_completed=True)
        confirmation = self.extractions.create_confirmation(
            case_id=case.id,
            extraction_run_id=run.id,
            confirmed_by_user_id=user.id,
            confirmation_status=confirmation_status,
            pending_fields_count=pending_count,
            accepted_low_confidence_fields=payload.accept_low_confidence_fields,
            user_statement=payload.user_statement,
            confirmed_at=now,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if case.status == "documents_uploaded":
            validate_case_transition(
                self.db,
                case,
                new_status="preanalysis_pending",
                validate_transition=True,
            )
            previous_status = case.status
            current_step, next_best_action = step_for_status("preanalysis_pending")
            case.status = "preanalysis_pending"
            case.current_step = current_step
            case.next_best_action = next_best_action
            case.updated_at = now
            self.cases.create_status_history(
                case_id=case.id,
                previous_status=previous_status,
                new_status=case.status,
                reason="Extraccion confirmada por el usuario.",
                changed_by_user_id=user.id,
                changed_by_role="user",
                source_module="documents",
                metadata={"origin": "extraction_confirmation"},
            )
        event_name = (
            "extraction.confirmed_with_pending_fields"
            if confirmation_status == "confirmed_with_pending_fields"
            else "extraction.confirmed"
        )
        self._audit(
            event_name,
            actor=user,
            case=case,
            entity_type="extraction_confirmation",
            entity_id=confirmation.id,
            previous_state=previous_state,
            new_state=self._run_state(run),
            metadata={
                "pendingFieldsCount": pending_count,
                "acceptedLowConfidenceFields": payload.accept_low_confidence_fields,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._record_history_event(
            case=case,
            event_type="extraccion_validacion_datos.submitted",
            title="Datos extraidos confirmados",
            description="El usuario confirmo los datos estructurados de la extraccion.",
            severity="success",
            actor=user,
            metadata={"confirmationStatus": confirmation_status},
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "confirmationStatus": confirmation_status,
            "confirmedAt": now,
            "nextStep": "preliminary_analysis",
        }

    def list_corrections(
        self,
        case_id: str,
        *,
        user: User,
        entity_type: str | None,
        entity_id: str | None,
        field_key: str | None,
        page: int,
        limit: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        parsed_entity_id = _parse_uuid(entity_id)
        if entity_id and parsed_entity_id is None:
            raise self._invalid_value("entityId debe ser UUID.")
        items, total = self.extractions.list_corrections(
            case_id=case.id,
            entity_type=_canonical_entity_type(entity_type) if entity_type else None,
            entity_id=parsed_entity_id,
            field_key=_canonical_field_key(field_key) if field_key else None,
            page=page,
            limit=limit,
        )
        return {
            "items": [self._correction_response(item) for item in items],
            "pagination": {"page": page, "limit": limit, "total": total},
        }

    def list_issues(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        return {"items": [self._issue_response(item) for item in self.extractions.list_issues(case.id)]}

    def resolve_issue(
        self,
        case_id: str,
        issue_id: str,
        payload: IssueResolveRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        issue = self.extractions.get_issue(issue_id)
        if issue is None or issue.case_id != case.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="EXTRACTION_NOT_FOUND",
                message="Issue de extraccion no encontrado.",
            )
        previous_state = self._issue_state(issue)
        issue.status = payload.status
        issue.resolution_note = payload.resolution_note
        issue.resolved_by_user_id = user.id
        issue.resolved_at = utc_now()
        issue.updated_at = utc_now()
        run = self.extractions.get_run(issue.extraction_run_id) if issue.extraction_run_id else None
        if run is not None:
            self._recalculate_run(run)
        self._audit(
            "extraction.issue.resolved",
            actor=user,
            case=case,
            entity_type="extraction_issue",
            entity_id=issue.id,
            previous_state=previous_state,
            new_state=self._issue_state(issue),
            metadata={"resolutionNote": payload.resolution_note},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"id": str(issue.id), "status": issue.status, "resolvedAt": issue.resolved_at}

    def can_case_proceed_to_analysis(self, case_id: str) -> bool:
        case = self._get_case_or_404(case_id)
        run = self.extractions.latest_run(case.id)
        if run is None:
            return False
        return run.confirmation_status in {"user_confirmed", "confirmed_with_pending_fields", "admin_approved"}

    def _run_job(
        self,
        *,
        case: LaboraCase,
        run: ExtractionRun,
        job: ExtractionJob,
        documents: list[Document],
        questionnaire_id: uuid.UUID | None,
        provider_name: str,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        job.status = "running"
        job.started_at = utc_now()
        job.progress = Decimal("10.00")
        job.logs = [{"at": utc_now().isoformat(), "message": "Job started."}]
        try:
            provider = extraction_provider_factory(provider_name)
            input_payload = self._provider_input(
                case=case,
                documents=documents,
                questionnaire_id=questionnaire_id,
            )
            result = provider.extract_labor_data(input_payload)
            job.progress = Decimal("65.00")
            self._persist_provider_output(case=case, run=run, output=result.output)
            job.progress = Decimal("90.00")
            self._recalculate_run(run)
            job.status = "completed"
            job.progress = Decimal("100.00")
            job.completed_at = utc_now()
            job.updated_at = utc_now()
            job.logs = [*job.logs, {"at": utc_now().isoformat(), "message": "Job completed."}]
            run.ai_provider = result.provider
            run.ai_model = result.model
            run.completed_at = utc_now()
            run.updated_at = utc_now()
            if run.confirmation_status == "draft":
                run.confirmation_status = "ai_extracted"
            self._audit(
                "extraction.run.completed",
                actor=actor,
                case=case,
                entity_type="extraction_run",
                entity_id=run.id,
                new_state=self._run_state(run),
                metadata={"jobId": str(job.id), "latencyMs": result.latency_ms},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except (AiExtractionProviderError, ExtractionNormalizationError) as exc:
            run.status = "error"
            run.error_code = getattr(exc, "code", "INTERNAL_PROCESSING_ERROR")
            run.error_message = str(exc)
            run.completed_at = utc_now()
            job.status = "failed"
            job.error_code = run.error_code
            job.error_message = run.error_message
            job.completed_at = utc_now()
            self._audit(
                "extraction.run.failed",
                actor=actor,
                case=case,
                entity_type="extraction_run",
                entity_id=run.id,
                new_state=self._run_state(run),
                metadata={"jobId": str(job.id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise ApiError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="AI_PROVIDER_ERROR"
                if isinstance(exc, AiExtractionProviderError)
                else "INTERNAL_PROCESSING_ERROR",
                message="No fue posible completar la extraccion.",
                details={"errorCode": run.error_code},
            ) from exc

    def _persist_provider_output(
        self,
        *,
        case: LaboraCase,
        run: ExtractionRun,
        output: dict[str, list[dict[str, Any]]],
    ) -> None:
        employer_by_name: dict[str, Employer] = {
            employer.name.lower(): employer
            for employer in self.extractions.list_employers(case.id)
            if employer.status != "ignored"
        }
        created_employers: list[Employer] = []
        for item in output.get("employers", []):
            name = normalize_employer_name(str(item.get("name") or item.get("rawName") or "Empleador por validar"))
            nit = _blank_to_none(item.get("nit"))
            employer = employer_by_name.get(name.lower())
            if employer is None:
                confidence = _confidence(item.get("confidence"))
                employer = self.extractions.create_employer(
                    case_id=case.id,
                    extraction_run_id=run.id,
                    name=name,
                    raw_name=_blank_to_none(item.get("rawName")) or name,
                    nit=nit,
                    employer_type=_blank_to_none(item.get("employerType")) or "unknown",
                    confidence=confidence,
                    status=status_for_confidence(confidence),
                    source=_blank_to_none(item.get("source")) or "ai",
                )
                employer_by_name[name.lower()] = employer
            elif not _is_user_protected(employer.status):
                employer.confidence = _confidence(item.get("confidence")) or employer.confidence
                employer.updated_at = utc_now()
            created_employers.append(employer)

        period_by_key: dict[tuple, LaborPeriod] = {}
        for period in self.extractions.list_labor_periods(case.id):
            period_by_key[(period.employer_id, period.start_date, period.end_date, period.period_type)] = period
        created_periods: list[LaborPeriod] = []
        for item in output.get("laborPeriods", []):
            employer = self._resolve_output_employer(item, employer_by_name, created_employers)
            start_date = normalize_date_value(item.get("startDate") or item.get("start_date"))
            end_raw = item.get("endDate") if "endDate" in item else item.get("end_date")
            end_date = normalize_date_value(end_raw) if end_raw else None
            validate_labor_period_range(start_date, end_date)
            period_type = _blank_to_none(item.get("periodType")) or "reported"
            key = (employer.id if employer else None, start_date, end_date, period_type)
            period = period_by_key.get(key)
            confidence = _confidence(item.get("confidence"))
            if period is None:
                period = self.extractions.create_labor_period(
                    case_id=case.id,
                    extraction_run_id=run.id,
                    employer_id=employer.id if employer else None,
                    start_date=start_date,
                    end_date=end_date,
                    period_type=period_type,
                    regime_hint=_blank_to_none(item.get("regimeHint")) or "unknown",
                    weeks_detected=_optional_decimal(item.get("weeksDetected")),
                    days_detected=_optional_int(item.get("daysDetected")),
                    salary_base_detected=_optional_money(item.get("salaryBaseDetected")),
                    novelty=_blank_to_none(item.get("novelty")),
                    confidence=confidence,
                    status=status_for_confidence(confidence),
                    source=_blank_to_none(item.get("source")) or "ai",
                    source_document_id=_parse_uuid(item.get("sourceDocumentId")),
                    source_page=_optional_int(item.get("sourcePage")),
                )
                period_by_key[key] = period
            elif not _is_user_protected(period.status):
                period.confidence = confidence or period.confidence
                period.updated_at = utc_now()
            created_periods.append(period)
            if period.confidence is not None and is_blocking_confidence(period.confidence):
                self._create_low_confidence_issue(
                    case=case,
                    run=run,
                    entity_type="labor_period",
                    entity_id=period.id,
                    message="Periodo laboral con confianza critica.",
                )

        for item in output.get("contributionWeeks", []):
            confidence = _confidence(item.get("confidence"))
            period = created_periods[0] if created_periods else None
            employer = created_employers[0] if created_employers else None
            self.extractions.create_contribution_week(
                case_id=case.id,
                extraction_run_id=run.id,
                labor_period_id=period.id if period else None,
                employer_id=employer.id if employer else None,
                year=int(item["year"]),
                month=_optional_int(item.get("month")),
                weeks=normalize_decimal_value(item.get("weeks"), field_name="weeks"),
                days=_optional_int(item.get("days")),
                source=_blank_to_none(item.get("source")) or "document",
                confidence=confidence,
                status=status_for_confidence(confidence),
            )

        for item in output.get("salaryBases", []):
            confidence = _confidence(item.get("confidence"))
            period = created_periods[0] if created_periods else None
            employer = created_employers[0] if created_employers else None
            self.extractions.create_salary_base(
                case_id=case.id,
                extraction_run_id=run.id,
                labor_period_id=period.id if period else None,
                employer_id=employer.id if employer else None,
                period_year=int(item.get("periodYear") or item.get("year")),
                period_month=_optional_int(item.get("periodMonth") or item.get("month")),
                amount=normalize_money_value(item.get("amount")),
                currency=_blank_to_none(item.get("currency")) or "COP",
                raw_value=_blank_to_none(item.get("rawValue")),
                confidence=confidence,
                status=status_for_confidence(confidence),
            )

        for item in output.get("gaps", []):
            confidence = _confidence(item.get("confidence"))
            self.extractions.create_gap(
                case_id=case.id,
                extraction_run_id=run.id,
                start_date=normalize_date_value(item.get("startDate")),
                end_date=normalize_date_value(item.get("endDate")),
                gap_type=_blank_to_none(item.get("gapType")) or "unknown",
                description=_blank_to_none(item.get("description")),
                severity=_blank_to_none(item.get("severity")) or "medium",
                confidence=confidence,
                status=status_for_confidence(confidence),
            )

        for item in output.get("novelties", []):
            confidence = _confidence(item.get("confidence"))
            period = created_periods[0] if created_periods else None
            self.extractions.create_novelty(
                case_id=case.id,
                extraction_run_id=run.id,
                labor_period_id=period.id if period else None,
                novelty_type=_blank_to_none(item.get("noveltyType")) or "other",
                description=str(item.get("description") or "Novedad detectada."),
                detected_by=_blank_to_none(item.get("detectedBy")) or "ai",
                confidence=confidence,
                status=status_for_confidence(confidence),
            )

        for item in output.get("fields", []):
            entity_type = _canonical_entity_type(item.get("entityType") or "extraction_field")
            entity_id = _parse_uuid(item.get("entityId"))
            if entity_id is None and entity_type == "employer" and created_employers:
                entity_id = created_employers[0].id
            if entity_id is None and entity_type == "labor_period" and created_periods:
                entity_id = created_periods[0].id
            confidence = _confidence(item.get("confidence"))
            field_status = status_for_confidence(confidence)
            field = self.extractions.create_field(
                case_id=case.id,
                extraction_run_id=run.id,
                entity_type=entity_type,
                entity_id=entity_id,
                field_key=_canonical_field_key(item.get("fieldKey") or "value"),
                raw_value=_blank_to_none(item.get("rawValue")),
                normalized_value=item.get("value"),
                display_value=str(item.get("value")) if item.get("value") is not None else None,
                confidence=confidence,
                status=field_status,
                source_document_id=_parse_uuid(item.get("sourceDocumentId")),
                source_page=_optional_int(item.get("sourcePage")),
                source_bbox=item.get("sourceBbox") or item.get("sourceBBox"),
                source_text=short_source_text(item.get("sourceText")),
                extraction_method=_blank_to_none(item.get("extractionMethod")) or "ai",
                needs_review=needs_review_for_confidence(confidence),
            )
            if confidence is not None and is_blocking_confidence(confidence):
                self._create_low_confidence_issue(
                    case=case,
                    run=run,
                    entity_type=field.entity_type,
                    entity_id=field.entity_id or field.id,
                    field_id=field.id,
                    message=f"El campo {field.field_key} tiene confianza critica.",
                )

        for item in output.get("issues", []):
            self.extractions.create_issue(
                case_id=case.id,
                extraction_run_id=run.id,
                extraction_field_id=_parse_uuid(item.get("fieldId")),
                entity_type=_canonical_entity_type(item.get("entityType")) if item.get("entityType") else None,
                entity_id=_parse_uuid(item.get("entityId")),
                issue_type=_blank_to_none(item.get("type")) or "inconsistency",
                severity=_blank_to_none(item.get("severity")) or "medium",
                message=str(item.get("message") or "Alerta de extraccion."),
                status="open",
            )

    def _provider_input(
        self,
        *,
        case: LaboraCase,
        documents: list[Document],
        questionnaire_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        return {
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "holder": {
                "birthDate": case.holder_birth_date.isoformat()
                if case.holder_birth_date
                else None,
                "documentType": case.holder_document_type,
            },
            "documents": [
                {
                    "id": str(document.id),
                    "documentType": document.document_type.code if document.document_type else None,
                    "status": document.status,
                    "pages": [
                        {
                            "pageNumber": page.page_number,
                            "text": _truncate_text(page.text_extracted or ""),
                            "textConfidence": float(page.text_confidence)
                            if page.text_confidence is not None
                            else None,
                        }
                        for page in document.pages[:20]
                    ],
                }
                for document in documents
            ],
            "questionnaireResponseId": str(questionnaire_id) if questionnaire_id else None,
            "questionnaireAnswers": [],
        }

    def _validated_documents(
        self,
        *,
        case: LaboraCase,
        document_ids: list[str],
    ) -> list[Document]:
        if document_ids:
            documents: list[Document] = []
            for document_id in document_ids:
                document = self.documents.get(document_id)
                if (
                    document is None
                    or document.case_id != case.id
                    or document.deleted_at is not None
                    or document.status not in READY_DOCUMENT_STATUSES
                ):
                    raise ApiError(
                        status_code=status.HTTP_409_CONFLICT,
                        code="DOCUMENTS_NOT_READY",
                        message="Uno o mas documentos no estan aptos para extraccion.",
                    )
                documents.append(document)
            return documents
        return [
            document
            for document in self.documents.list_active_case_documents(case.id)
            if document.status in READY_DOCUMENT_STATUSES
        ]

    def _get_or_create_manual_run(
        self,
        *,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ExtractionRun:
        run = self.extractions.latest_run(case.id)
        if run is not None and run.confirmation_status not in {"rejected", "superseded"}:
            return run
        now = utc_now()
        run = self.extractions.create_run(
            case_id=case.id,
            status="completed",
            confirmation_status="user_reviewing",
            source="manual",
            document_ids=[],
            low_confidence_count=0,
            issues_count=0,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        self._audit(
            "extraccion_validacion_datos.created",
            actor=user,
            case=case,
            entity_type="extraction_run",
            entity_id=run.id,
            new_state=self._run_state(run),
            metadata={"source": "manual"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return run

    def _recalculate_run(self, run: ExtractionRun, *, keep_completed: bool = False) -> None:
        fields = [
            field
            for field in self.extractions.list_fields(run.case_id)
            if field.extraction_run_id == run.id and field.status != "ignored"
        ]
        confidences = [field.confidence for field in fields if field.confidence is not None]
        low_count = sum(
            1
            for field in fields
            if field.status == "low_confidence" or field.needs_review
        )
        issues_count = (
            self.db.query(ExtractionIssue)
            .filter(
                ExtractionIssue.case_id == run.case_id,
                ExtractionIssue.extraction_run_id == run.id,
                ExtractionIssue.status == "open",
            )
            .count()
        )
        run.confidence_avg = (
            sum(confidences, Decimal("0.0000")) / Decimal(len(confidences))
            if confidences
            else None
        )
        run.low_confidence_count = low_count
        run.issues_count = issues_count
        if not keep_completed:
            run.status = "requires_review" if low_count > 0 or issues_count > 0 else "completed"
        run.updated_at = utc_now()

    def _create_low_confidence_issue(
        self,
        *,
        case: LaboraCase,
        run: ExtractionRun,
        entity_type: str,
        entity_id: uuid.UUID,
        message: str,
        field_id: uuid.UUID | None = None,
    ) -> None:
        self.extractions.create_issue(
            case_id=case.id,
            extraction_run_id=run.id,
            extraction_field_id=field_id,
            entity_type=entity_type,
            entity_id=entity_id,
            issue_type="low_confidence",
            severity="high",
            message=message,
            status="open",
        )

    def _normalize_field_value(
        self,
        *,
        entity_type: str,
        field_key: str,
        value: Any,
    ) -> tuple[Any, str]:
        canonical_key = _canonical_field_key(field_key)
        try:
            if canonical_key.endswith("date") or canonical_key in {"start_date", "end_date"}:
                normalized_date = normalize_date_value(value)
                return normalized_date.isoformat(), normalized_date.isoformat()
            if canonical_key in {"amount", "salary_base_detected", "salary", "base_salary"}:
                amount = normalize_money_value(value)
                return str(amount), f"{amount:.2f}"
            if canonical_key in {"weeks", "weeks_detected"}:
                weeks = normalize_decimal_value(value, field_name="weeks")
                return str(weeks), str(weeks)
            if canonical_key in {"days", "days_detected", "year", "month", "period_year", "period_month"}:
                parsed = int(value)
                if parsed < 0:
                    raise ExtractionNormalizationError("El valor numerico no puede ser negativo.")
                return parsed, str(parsed)
            if entity_type == "employer" and canonical_key == "name":
                name = normalize_employer_name(str(value))
                return name, name
        except (ExtractionNormalizationError, ValueError) as exc:
            raise ApiError(
                status_code=422,
                code="INVALID_FIELD_VALUE",
                message=str(exc),
            ) from exc
        if value is None:
            raise self._invalid_value("El nuevo valor no puede ser null.")
        display = str(value).strip()
        if not display:
            raise self._invalid_value("El nuevo valor no puede estar vacio.")
        return value, display

    def _apply_entity_update(self, field: ExtractionField, normalized_value: Any) -> None:
        if field.entity_id is None:
            return
        entity = self._get_entity_or_404(
            _canonical_entity_type(field.entity_type),
            str(field.entity_id),
            case_id=field.case_id,
        )
        attribute = _canonical_field_key(field.field_key)
        if not hasattr(entity, attribute):
            return
        if attribute.endswith("date") and normalized_value is not None:
            setattr(entity, attribute, normalize_date_value(normalized_value))
        elif attribute in {"amount", "salary_base_detected"}:
            setattr(entity, attribute, normalize_money_value(normalized_value))
        elif attribute in {"weeks", "weeks_detected"}:
            setattr(entity, attribute, normalize_decimal_value(normalized_value, field_name=attribute))
        elif attribute in {"days", "days_detected", "year", "month", "period_year", "period_month"}:
            setattr(entity, attribute, int(normalized_value))
        else:
            setattr(entity, attribute, normalized_value)
        if hasattr(entity, "status"):
            entity.status = field.status
        if hasattr(entity, "updated_at"):
            entity.updated_at = utc_now()
        if isinstance(entity, LaborPeriod):
            validate_labor_period_range(entity.start_date, entity.end_date)

    def _get_entity_or_404(
        self,
        entity_type: str,
        entity_id: str,
        *,
        case_id: uuid.UUID,
    ):
        model = ENTITY_MODELS.get(entity_type)
        parsed_id = _parse_uuid(entity_id)
        if model is None or parsed_id is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="EXTRACTION_NOT_FOUND",
                message="Entidad de extraccion no encontrada.",
            )
        entity = self.db.get(model, parsed_id)
        if entity is None or entity.case_id != case_id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="EXTRACTION_NOT_FOUND",
                message="Entidad de extraccion no encontrada.",
            )
        return entity

    def _low_confidence_fields(self, case_id: uuid.UUID) -> list[ExtractionField]:
        return (
            self.db.query(ExtractionField)
            .filter(
                ExtractionField.case_id == case_id,
                ExtractionField.status != "ignored",
                (
                    (ExtractionField.status == "low_confidence")
                    | (ExtractionField.needs_review.is_(True))
                ),
            )
            .all()
        )

    def _blocking_reasons(self, case_id: uuid.UUID) -> list[str]:
        reasons: list[str] = []
        high_issues = [
            issue
            for issue in self.extractions.list_issues(case_id)
            if issue.status == "open" and issue.severity in {"high", "critical"}
        ]
        if high_issues:
            reasons.append(
                f"Hay {len(high_issues)} alertas criticas o altas pendientes."
            )
        return reasons

    def _summary(self, case_id: uuid.UUID) -> dict[str, Any]:
        employers = [item for item in self.extractions.list_employers(case_id) if item.status != "ignored"]
        periods = [item for item in self.extractions.list_labor_periods(case_id) if item.status != "ignored"]
        weeks = [item for item in self.extractions.list_contribution_weeks(case_id) if item.status != "ignored"]
        salary_bases = [item for item in self.extractions.list_salary_bases(case_id) if item.status != "ignored"]
        gaps = [item for item in self.extractions.list_gaps(case_id) if item.status != "ignored"]
        novelties = [item for item in self.extractions.list_novelties(case_id) if item.status != "ignored"]
        return {
            "employersCount": len(employers),
            "laborPeriodsCount": len(periods),
            "contributionWeeksTotal": float(sum((item.weeks for item in weeks), Decimal("0.00"))),
            "salaryBasesCount": len(salary_bases),
            "gapsCount": len(gaps),
            "noveltiesCount": len(novelties),
        }

    def _extraction_payload(
        self,
        *,
        case: LaboraCase,
        run: ExtractionRun | None,
        user: User,
    ) -> dict[str, Any]:
        employers = self.extractions.list_employers(case.id)
        employer_by_id = {str(employer.id): employer for employer in employers}
        periods = self.extractions.list_labor_periods(case.id)
        contribution_weeks = self.extractions.list_contribution_weeks(case.id)
        salary_bases = self.extractions.list_salary_bases(case.id)
        gaps = self.extractions.list_gaps(case.id)
        novelties = self.extractions.list_novelties(case.id)
        fields = self.extractions.list_fields(case.id)
        issues = self.extractions.list_issues(case.id)
        blocking_reasons = self._blocking_reasons(case.id)
        status_value = run.status if run else "not_started"
        if status_value == "in_progress":
            blocking_reasons = ["La extraccion documental sigue en proceso.", *blocking_reasons]
        if status_value == "error" and run and run.error_message:
            blocking_reasons = [run.error_message, *blocking_reasons]
        confirmation_status = run.confirmation_status if run else "draft"
        can_confirm = (
            run is not None
            and run.status in {"completed", "requires_review"}
            and not blocking_reasons
            and run.confirmation_status
            not in {"user_confirmed", "confirmed_with_pending_fields", "admin_approved"}
            and self._can_confirm(case, user)
        )
        employers_payload = [self._employer_response(item) for item in employers]
        periods_payload = [self._labor_period_response(item, employer_by_id) for item in periods]
        contribution_weeks_payload = [self._contribution_week_response(item, employer_by_id) for item in contribution_weeks]
        salary_bases_payload = [self._salary_base_response(item, employer_by_id) for item in salary_bases]
        gaps_payload = [self._gap_response(item) for item in gaps]
        novelties_payload = [self._novelty_response(item) for item in novelties]
        return {
            "caseId": str(case.id),
            "status": status_value,
            "moduleStatus": status_value,
            "confirmationStatus": confirmation_status,
            "confidenceAvg": float(run.confidence_avg) if run and run.confidence_avg is not None else None,
            "lowConfidenceCount": run.low_confidence_count if run else 0,
            "summary": self._summary(case.id),
            "employers": employers_payload,
            "laborPeriods": periods_payload,
            "contributionWeeks": contribution_weeks_payload,
            "salaryBases": salary_bases_payload,
            "gaps": gaps_payload,
            "novelties": novelties_payload,
            "fields": [self._field_response(item) for item in fields],
            "issues": [self._issue_response(item) for item in issues],
            "timeline": [
                self._timeline_item(period, employer_by_id, issues)
                for period in periods
                if period.status != "ignored"
            ],
            "tables": {
                "employers": employers_payload,
                "laborPeriods": periods_payload,
                "contributionWeeks": contribution_weeks_payload,
                "salaryBases": salary_bases_payload,
                "gaps": gaps_payload,
            },
            "documentReferences": [
                self._document_reference(
                    field.source_document_id,
                    page=field.source_page,
                    bbox=field.source_bbox,
                    field_id=field.id,
                    source_text=field.source_text,
                )
                for field in fields
                if field.source_document_id is not None
            ],
            "actions": {
                "canEdit": self._can_update(case, user),
                "canConfirm": can_confirm,
                "canRequestReview": True,
            },
            "canConfirm": can_confirm,
            "blockingReasons": blocking_reasons,
        }

    def _find_reusable_run(
        self,
        *,
        case_id: uuid.UUID,
        document_ids: list[str],
        questionnaire_id: uuid.UUID | None,
        reusable_statuses: set[str],
    ) -> ExtractionRun | None:
        wanted_document_ids = set(document_ids)
        for run in self.extractions.list_runs(case_id):
            if run.status not in reusable_statuses:
                continue
            if set(run.document_ids or []) != wanted_document_ids:
                continue
            if run.questionnaire_response_id != questionnaire_id:
                continue
            return run
        return None

    def _primary_labor_history_document(self, case: LaboraCase) -> Document | None:
        documents = [
            document
            for document in self.documents.list_active_case_documents(case.id)
            if self._is_labor_history_document(document)
            and document.status in READY_DOCUMENT_STATUSES
        ]
        primary = [document for document in documents if document.is_primary]
        return (primary or documents)[0] if documents else None

    def _is_labor_history_document(self, document: Document) -> bool:
        document_type_code = document.document_type.code if document.document_type else None
        return document_type_code == "historia_laboral" or document.is_primary is True

    def _document_reference(
        self,
        document_id: uuid.UUID | None,
        *,
        page: int | None,
        bbox: dict | None = None,
        field_id: uuid.UUID | None = None,
        source_text: str | None = None,
    ) -> dict[str, Any] | None:
        if document_id is None:
            return None
        document = self.documents.get(document_id)
        return {
            "documentId": str(document_id),
            "documentName": document.display_name or document.original_filename if document else None,
            "page": page,
            "sourceText": source_text,
            "fieldId": str(field_id) if field_id else None,
            "bbox": bbox,
        }

    def _require_sensitive_consent(self, user: User) -> None:
        if user.role in {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, "system"}:
            return
        permission = ConsentComplianceService(self.db).can_upload_documents(user.id)
        if not permission.allowed:
            raise ApiError(
                status_code=422,
                code="CONSENT_REQUIRED",
                message="Debes aceptar los consentimientos requeridos antes de iniciar extraccion.",
                details={
                    "missingConsentTypes": permission.missing_consent_types,
                    "reason": permission.reason,
                },
            )

    def _require_can_view(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_view(case, user):
            return
        self._audit_access_denied(case=case, actor=user, action="view", ip_address=ip_address, user_agent=user_agent)

    def _require_can_update(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_update(case, user):
            return
        self._audit_access_denied(case=case, actor=user, action="update", ip_address=ip_address, user_agent=user_agent)

    def _require_can_start(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if user.role == "system" or self._is_admin(user) or case.owner_user_id == user.id:
            return
        self._audit_access_denied(case=case, actor=user, action="start", ip_address=ip_address, user_agent=user_agent)

    def _require_can_confirm(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_confirm(case, user):
            return
        self._audit_access_denied(case=case, actor=user, action="confirm", ip_address=ip_address, user_agent=user_agent)

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return (
                case.status == "requires_review"
                or self.cases.get_owner(
                    case_id=case.id,
                    user_id=user.id,
                    roles={"legal_reviewer"},
                )
                is not None
            )
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
        if user.role in LEGAL_REVIEWER_ROLES:
            return (
                self.cases.get_owner(
                    case_id=case.id,
                    user_id=user.id,
                    roles={"legal_reviewer"},
                )
                is not None
            )
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _can_confirm(self, case: LaboraCase, user: User) -> bool:
        return self._is_admin(user) or case.owner_user_id == user.id

    def _require_case_allows_writes(self, case: LaboraCase, user: User) -> None:
        if self._is_admin(user):
            return
        if case.status in LOCKED_STATUSES:
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="CONFIRMATION_BLOCKED",
                message="El estado del expediente no permite modificar extraccion.",
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

    def _is_admin(self, user: User) -> bool:
        return user.role in ADMIN_ROLES

    def _actor_type(self, user: User) -> str:
        if self._is_admin(user):
            return "admin"
        if user.role in LEGAL_REVIEWER_ROLES:
            return "legal_reviewer"
        if user.role == "system":
            return "system"
        return "user"

    def _audit_access_denied(
        self,
        *,
        case: LaboraCase,
        actor: User,
        action: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._audit(
            "extraccion_validacion_datos.access_denied",
            actor=actor,
            case=case,
            entity_type="case",
            entity_id=case.id,
            metadata={"action": action},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CASE_ACCESS_DENIED",
            message="No tienes permisos para acceder a este expediente.",
        )

    def _audit(
        self,
        event_name: str,
        *,
        actor: User,
        case: LaboraCase,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_payload = {
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "actorType": self._actor_type(actor),
            "sourceModule": "extraction_validation",
            **(metadata or {}),
        }
        self.audit_events.create(
            event_type=event_name,
            entity_type=entity_type or "case",
            entity_id=entity_id,
            actor_user_id=actor.id,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata=_json_safe(metadata_payload),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.extractions.create_audit_event(
            event_name=event_name,
            case_id=case.id,
            actor_type=self._actor_type(actor),
            actor_id=actor.id,
            entity_type=entity_type,
            entity_id=entity_id,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata_json=_json_safe(metadata_payload),
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _record_history_event(
        self,
        *,
        case: LaboraCase,
        event_type: str,
        title: str,
        description: str | None,
        severity: str,
        actor: User,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.db.add(
            CaseHistoryEvent(
                case_id=case.id,
                event_type=event_type,
                title=title,
                description=description,
                visibility="both",
                severity=severity,
                created_by_user_id=actor.id,
                metadata_json=_json_safe(metadata) if metadata else None,
            )
        )
        self.db.flush()

    def _idempotency_key(
        self,
        *,
        case_id: uuid.UUID,
        document_ids: list[str],
        questionnaire_id: uuid.UUID | None,
        mode: str,
    ) -> str:
        raw = json.dumps(
            {
                "caseId": str(case_id),
                "documentIds": sorted(document_ids),
                "questionnaireId": str(questionnaire_id) if questionnaire_id else None,
                "mode": mode,
                "version": "v1",
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _invalid_value(self, message: str) -> ApiError:
        return ApiError(
            status_code=422,
            code="INVALID_FIELD_VALUE",
            message=message,
        )

    def _resolve_output_employer(
        self,
        item: dict[str, Any],
        employer_by_name: dict[str, Employer],
        created_employers: list[Employer],
    ) -> Employer | None:
        if item.get("employerId"):
            parsed = _parse_uuid(item.get("employerId"))
            if parsed is not None:
                employer = self.db.get(Employer, parsed)
                if employer is not None:
                    return employer
        employer_name = _blank_to_none(item.get("employerName"))
        if employer_name:
            return employer_by_name.get(normalize_employer_name(employer_name).lower())
        return created_employers[0] if created_employers else None

    def _run_state(self, run: ExtractionRun) -> dict[str, Any]:
        return {
            "id": str(run.id),
            "caseId": str(run.case_id),
            "status": run.status,
            "confirmationStatus": run.confirmation_status,
            "confidenceAvg": run.confidence_avg,
            "lowConfidenceCount": run.low_confidence_count,
            "issuesCount": run.issues_count,
        }

    def _field_state(self, field: ExtractionField) -> dict[str, Any]:
        return {
            "id": str(field.id),
            "caseId": str(field.case_id),
            "entityType": field.entity_type,
            "entityId": str(field.entity_id) if field.entity_id else None,
            "fieldKey": field.field_key,
            "normalizedValue": field.normalized_value,
            "displayValue": field.display_value,
            "confidence": field.confidence,
            "status": field.status,
            "needsReview": field.needs_review,
        }

    def _employer_state(self, employer: Employer) -> dict[str, Any]:
        return {
            "id": str(employer.id),
            "name": employer.name,
            "rawName": employer.raw_name,
            "nit": employer.nit,
            "employerType": employer.employer_type,
            "confidence": employer.confidence,
            "status": employer.status,
            "source": employer.source,
        }

    def _period_state(self, period: LaborPeriod) -> dict[str, Any]:
        return {
            "id": str(period.id),
            "employerId": str(period.employer_id) if period.employer_id else None,
            "startDate": period.start_date,
            "endDate": period.end_date,
            "periodType": period.period_type,
            "weeksDetected": period.weeks_detected,
            "status": period.status,
        }

    def _issue_state(self, issue: ExtractionIssue) -> dict[str, Any]:
        return {
            "id": str(issue.id),
            "type": issue.issue_type,
            "severity": issue.severity,
            "message": issue.message,
            "status": issue.status,
            "resolvedAt": issue.resolved_at,
        }

    def _entity_state(self, entity) -> dict[str, Any]:
        if isinstance(entity, ExtractionField):
            return self._field_state(entity)
        if isinstance(entity, Employer):
            return self._employer_state(entity)
        if isinstance(entity, LaborPeriod):
            return self._period_state(entity)
        return {
            "id": str(entity.id),
            "status": getattr(entity, "status", None),
        }

    def _field_response(self, field: ExtractionField) -> dict[str, Any]:
        return {
            "id": str(field.id),
            "entityType": field.entity_type,
            "entityId": str(field.entity_id) if field.entity_id else None,
            "fieldKey": field.field_key,
            "rawValue": field.raw_value,
            "normalizedValue": field.normalized_value,
            "displayValue": field.display_value,
            "confidence": float(field.confidence) if field.confidence is not None else None,
            "status": field.status,
            "sourceDocumentId": str(field.source_document_id) if field.source_document_id else None,
            "sourcePage": field.source_page,
            "sourceBbox": field.source_bbox,
            "sourceText": field.source_text,
            "extractionMethod": field.extraction_method,
            "needsReview": field.needs_review,
        }

    def _employer_response(self, employer: Employer) -> dict[str, Any]:
        return {
            "id": str(employer.id),
            "name": employer.name,
            "rawName": employer.raw_name,
            "nit": employer.nit,
            "employerType": employer.employer_type,
            "confidence": float(employer.confidence) if employer.confidence is not None else None,
            "status": employer.status,
            "source": employer.source,
        }

    def _labor_period_response(
        self,
        period: LaborPeriod,
        employers_by_id: dict[str, Employer],
    ) -> dict[str, Any]:
        employer = employers_by_id.get(str(period.employer_id)) if period.employer_id else None
        source = self._document_reference(period.source_document_id, page=period.source_page)
        return {
            "id": str(period.id),
            "employerId": str(period.employer_id) if period.employer_id else None,
            "employerName": employer.name if employer else None,
            "startDate": period.start_date,
            "endDate": period.end_date,
            "periodType": period.period_type,
            "regimeHint": period.regime_hint,
            "weeksDetected": float(period.weeks_detected) if period.weeks_detected is not None else None,
            "daysDetected": period.days_detected,
            "salaryBaseDetected": float(period.salary_base_detected) if period.salary_base_detected is not None else None,
            "novelty": period.novelty,
            "confidence": float(period.confidence) if period.confidence is not None else None,
            "status": period.status,
            "sourceDocumentId": str(period.source_document_id) if period.source_document_id else None,
            "sourcePage": period.source_page,
            "source": source,
        }

    def _contribution_week_response(self, item: ContributionWeek, employers_by_id: dict[str, Employer]) -> dict[str, Any]:
        employer = employers_by_id.get(str(item.employer_id)) if item.employer_id else None
        return {
            "id": str(item.id),
            "laborPeriodId": str(item.labor_period_id) if item.labor_period_id else None,
            "employerId": str(item.employer_id) if item.employer_id else None,
            "employerName": employer.name if employer else None,
            "year": item.year,
            "month": item.month,
            "weeks": float(item.weeks),
            "days": item.days,
            "source": item.source,
            "confidence": float(item.confidence) if item.confidence is not None else None,
            "status": item.status,
        }

    def _salary_base_response(self, item: SalaryBase, employers_by_id: dict[str, Employer]) -> dict[str, Any]:
        employer = employers_by_id.get(str(item.employer_id)) if item.employer_id else None
        return {
            "id": str(item.id),
            "laborPeriodId": str(item.labor_period_id) if item.labor_period_id else None,
            "employerId": str(item.employer_id) if item.employer_id else None,
            "employerName": employer.name if employer else None,
            "year": item.period_year,
            "month": item.period_month,
            "periodYear": item.period_year,
            "periodMonth": item.period_month,
            "amount": float(item.amount),
            "originalValue": float(item.amount),
            "normalizedValue": float(item.amount),
            "currency": item.currency,
            "rawValue": item.raw_value,
            "confidence": float(item.confidence) if item.confidence is not None else None,
            "status": item.status,
        }

    def _gap_response(self, item: ContributionGap) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "startDate": item.start_date,
            "endDate": item.end_date,
            "days": (item.end_date - item.start_date).days + 1 if item.start_date and item.end_date else None,
            "weeks": round(((item.end_date - item.start_date).days + 1) / 7, 2) if item.start_date and item.end_date else None,
            "reason": item.description,
            "gapType": item.gap_type,
            "description": item.description,
            "severity": item.severity,
            "confidence": float(item.confidence) if item.confidence is not None else None,
            "status": item.status,
        }

    def _novelty_response(self, item: LaborNovelty) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "laborPeriodId": str(item.labor_period_id) if item.labor_period_id else None,
            "noveltyType": item.novelty_type,
            "description": item.description,
            "detectedBy": item.detected_by,
            "confidence": float(item.confidence) if item.confidence is not None else None,
            "status": item.status,
        }

    def _issue_response(self, issue: ExtractionIssue) -> dict[str, Any]:
        return {
            "id": str(issue.id),
            "type": issue.issue_type,
            "severity": issue.severity,
            "message": issue.message,
            "fieldId": str(issue.extraction_field_id) if issue.extraction_field_id else None,
            "entityType": issue.entity_type,
            "entityId": str(issue.entity_id) if issue.entity_id else None,
            "resolved": issue.status in {"resolved", "dismissed"},
            "status": issue.status,
        }

    def _timeline_item(
        self,
        period: LaborPeriod,
        employers_by_id: dict[str, Employer],
        issues: list[ExtractionIssue],
    ) -> dict[str, Any]:
        employer = employers_by_id.get(str(period.employer_id)) if period.employer_id else None
        period_issues = [
            self._issue_response(issue)
            for issue in issues
            if issue.entity_type == "labor_period" and issue.entity_id == period.id
        ]
        return {
            "id": str(period.id),
            "startDate": period.start_date,
            "endDate": period.end_date,
            "employerName": employer.name if employer else None,
            "weeks": float(period.weeks_detected) if period.weeks_detected is not None else None,
            "confidence": float(period.confidence) if period.confidence is not None else None,
            "status": period.status,
            "issues": period_issues,
        }

    def _correction_response(self, correction) -> dict[str, Any]:
        user = self.db.get(User, correction.corrected_by_user_id)
        return {
            "id": str(correction.id),
            "fieldKey": correction.field_key,
            "previousValue": correction.previous_value,
            "newValue": correction.new_value,
            "reason": correction.reason,
            "correctedBy": {
                "id": str(correction.corrected_by_user_id),
                "name": user.full_name if user and user.full_name else user.email if user else None,
            },
            "createdAt": correction.created_at,
        }


def _job_type_for_mode(mode: str) -> str:
    return {
        "initial": "extract_case_labor_data",
        "reprocess": "extract_case_labor_data",
        "normalize_only": "normalize_extraction_fields",
    }[mode]


def _canonical_field_key(value: str | None) -> str:
    if not value:
        return "value"
    return FIELD_ALIASES.get(value, value).strip().replace("-", "_")


def _canonical_entity_type(value: str | None) -> str:
    if not value:
        return "extraction_field"
    return str(value).strip().replace("-", "_")


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    return normalized or None


def _confidence(value: Any) -> Decimal | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    if parsed < 0:
        parsed = Decimal("0")
    if parsed > 1:
        parsed = Decimal("1")
    return parsed.quantize(Decimal("0.0001"))


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return normalize_decimal_value(value, field_name="valor")


def _optional_money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return normalize_money_value(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _is_user_protected(status_value: str) -> bool:
    if not settings.extraction_preserve_user_corrections:
        return False
    return status_value in {
        "corrected_by_user",
        "corrected_by_admin",
        "confirmed",
        "pending_user_confirmation",
    }


def _truncate_text(value: str, limit: int = 4000) -> str:
    normalized = " ".join(value.strip().split())
    return normalized[:limit]


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
