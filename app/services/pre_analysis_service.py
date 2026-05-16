import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.audit_event import AuditEvent
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.document import Document, DocumentValidation
from app.models.extraction import (
    ContributionGap,
    Employer,
    ExtractionField,
    ExtractionRun,
    LaborPeriod,
    SalaryBase,
)
from app.models.pre_analysis import PreAnalysis, PreAnalysisJob, PreViability
from app.models.questionnaire import (
    CaseProfile,
    CaseQuestionnaireSession,
    QuestionnaireAnswer,
)
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.extraction_repository import ExtractionRepository
from app.repositories.pre_analysis_repository import PreAnalysisRepository
from app.services.case_service import step_for_status
from app.services.consent_service import ConsentComplianceService
from app.services.pre_analysis_ai_provider import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    PreAnalysisInvalidResponseError,
    PreAnalysisProviderError,
    pre_analysis_provider_factory,
)
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
PRE_ANALYSIS_REVIEW_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, "legal_ops", "reviewer"}
LOCKED_CASE_STATUSES = {"closed", "archived"}
COMPATIBLE_VALIDATION_RESULTS = {"accepted", "accepted_with_warnings"}
READY_DOCUMENT_STATUSES = {"uploaded", "validated", "requires_review"}
READY_EXTRACTION_STATUSES = {"completed", "requires_review"}
RETRYABLE_STATUSES = {"error", "blocked"}
LOW_CONFIDENCE_THRESHOLD = Decimal("0.7000")
EXTRACTOR_VERSION = "extraction-summary-v1"

CTA_COPY = {
    "unlock_full_analysis": {
        "label": "Desbloquear analisis completo",
        "description": "El analisis completo incluye calculo, fundamentos y reporte tecnico.",
    },
    "upload_missing_docs": {
        "label": "Cargar documentos faltantes",
        "description": "Completar los soportes puede mejorar la calidad del analisis.",
    },
    "wait_review": {
        "label": "Esperar revision",
        "description": "Un operador debe revisar el resultado preliminar antes de continuarlo.",
    },
}

PRE_ANALYSIS_EVENTS = {
    "created": "analisis_preliminar_gratuito.created",
    "queued": "analisis_preliminar_gratuito.queued",
    "started": "analisis_preliminar_gratuito.started",
    "updated": "analisis_preliminar_gratuito.updated",
    "completed": "analisis_preliminar_gratuito.completed",
    "blocked": "analisis_preliminar_gratuito.blocked",
    "requires_review": "analisis_preliminar_gratuito.requires_review",
    "failed": "analisis_preliminar_gratuito.failed",
    "viewed": "analisis_preliminar_gratuito.viewed",
    "retry_requested": "analisis_preliminar_gratuito.retry_requested",
    "reviewed": "analisis_preliminar_gratuito.reviewed",
}

PROHIBITED_KEYS = {
    "detailedLegalBasis",
    "fullCalculation",
    "retroactiveAmount",
    "finalClaimValue",
    "legalStrategy",
    "lawsuitRecommendationDetail",
    "legalBasis",
    "calculation",
    "formula",
    "retroactive",
    "claimValue",
}
PROHIBITED_TEXT_PATTERNS = [
    re.compile(r"\$\s?\d", flags=re.IGNORECASE),
    re.compile(r"\bretroactivo\b", flags=re.IGNORECASE),
    re.compile(r"\bliquidaci[oó]n\b", flags=re.IGNORECASE),
    re.compile(r"\bf[oó]rmula\b", flags=re.IGNORECASE),
    re.compile(r"\bjurisprudencia\b", flags=re.IGNORECASE),
    re.compile(r"\bsentencia\b", flags=re.IGNORECASE),
    re.compile(r"\bart[ií]culo\s+\d+", flags=re.IGNORECASE),
    re.compile(r"\bpretensi[oó]n\b", flags=re.IGNORECASE),
    re.compile(r"\bdemanda\b", flags=re.IGNORECASE),
]


class PreAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.documents = DocumentRepository(db)
        self.extractions = ExtractionRepository(db)
        self.pre_analysis = PreAnalysisRepository(db)
        self.audit_events = AuditEventRepository(db)

    def create_or_reuse(
        self,
        case_id: str,
        *,
        force_regenerate: bool,
        source: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        if force_regenerate and not self._is_admin(user):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="PERMISSION_DENIED",
                message="No tienes permisos para forzar regeneracion del preanalisis.",
            )

        inputs = self.validate_can_run(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)
        input_hash = self._input_hash(case=case, documents=inputs["documents"], extraction_run=inputs["extractionRun"])

        if not force_regenerate:
            existing_completed = self.pre_analysis.latest_completed_by_hash(
                case_id=case.id,
                input_hash=input_hash,
            )
            if existing_completed is not None:
                self._audit(
                    PRE_ANALYSIS_EVENTS["viewed"],
                    actor=user,
                    case=case,
                    pre_analysis=existing_completed,
                    metadata={"reused": True, "source": source},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                self.db.commit()
                return (
                    {
                        "preAnalysisId": str(existing_completed.id),
                        "caseId": str(case.id),
                        "status": existing_completed.status,
                        "resultAvailable": True,
                    },
                    status.HTTP_200_OK,
                )

            active = self.pre_analysis.active_for_case(case.id)
            if active is not None:
                if active.input_hash == input_hash:
                    return (
                        {
                            "preAnalysisId": str(active.id),
                            "caseId": str(case.id),
                            "status": active.status,
                            "message": "Estamos preparando tu analisis preliminar.",
                            "pollingUrl": f"/cases/{case.id}/pre-analysis",
                        },
                        status.HTTP_202_ACCEPTED,
                    )
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="PRE_ANALYSIS_ALREADY_RUNNING",
                    message="Ya hay un preanalisis en curso para este expediente.",
                )

        now = utc_now()
        provider_name = settings.ai_pre_analysis_provider
        provider_model = settings.ai_pre_analysis_model or "mock-pre-analysis-v1"
        pre_analysis = self.pre_analysis.create(
            case_id=case.id,
            user_id=case.owner_user_id,
            status="queued",
            input_hash=input_hash,
            prompt_version=PROMPT_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            ai_provider=provider_name,
            ai_model=provider_model,
            cta_type="unlock_full_analysis",
            created_at=now,
            updated_at=now,
        )
        job = self.pre_analysis.create_job(
            pre_analysis_id=pre_analysis.id,
            case_id=case.id,
            status="queued",
            attempts=0,
            max_attempts=settings.pre_analysis_job_max_attempts,
            progress=Decimal("0.00"),
            current_step="Esperando procesamiento",
            queued_at=now,
        )
        self._transition_case(case, "preanalysis_pending", reason="Preanalisis preliminar en cola.")
        self._record_history_event(
            case=case,
            event_type=PRE_ANALYSIS_EVENTS["created"],
            title="Preanalisis preliminar iniciado",
            description="Se puso en cola el analisis preliminar gratuito.",
            severity="info",
            actor=user,
            metadata={"preAnalysisId": str(pre_analysis.id), "jobId": str(job.id)},
        )
        self._audit(
            PRE_ANALYSIS_EVENTS["created"],
            actor=user,
            case=case,
            pre_analysis=pre_analysis,
            new_status=pre_analysis.status,
            metadata={"source": source},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            PRE_ANALYSIS_EVENTS["queued"],
            actor=user,
            case=case,
            pre_analysis=pre_analysis,
            new_status=pre_analysis.status,
            metadata={"jobId": str(job.id), "source": source},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return (
            {
                "preAnalysisId": str(pre_analysis.id),
                "caseId": str(case.id),
                "status": "queued",
                "message": "Estamos preparando tu analisis preliminar.",
                "pollingUrl": f"/cases/{case.id}/pre-analysis",
            },
            status.HTTP_202_ACCEPTED,
        )

    def get_user_facing_result(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        pre_analysis = self.pre_analysis.latest_for_case(case.id)
        if pre_analysis is None:
            return self._not_started_payload(case)
        self._audit(
            PRE_ANALYSIS_EVENTS["viewed"],
            actor=user,
            case=case,
            pre_analysis=pre_analysis,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._user_result_payload(pre_analysis)

    def get_status(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        pre_analysis = self.pre_analysis.latest_for_case(case.id)
        if pre_analysis is None:
            return {
                "caseId": str(case.id),
                "preAnalysisId": None,
                "status": "not_started",
                "progress": 0,
                "currentStep": "Sin iniciar",
                "canRetry": False,
            }
        job = self.pre_analysis.latest_job(pre_analysis.id)
        return {
            "caseId": str(case.id),
            "preAnalysisId": str(pre_analysis.id),
            "status": pre_analysis.status,
            "progress": _progress(pre_analysis, job),
            "currentStep": _current_step(pre_analysis, job),
            "canRetry": pre_analysis.status in RETRYABLE_STATUSES or self._is_admin(user),
        }

    def retry(
        self,
        case_id: str,
        *,
        reason: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        previous = self.pre_analysis.latest_for_case(case.id)
        if previous is None:
            return self.create_or_reuse(
                case_id,
                force_regenerate=False,
                source="retry_requested",
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        if previous.status not in RETRYABLE_STATUSES and not self._is_admin(user):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PRE_ANALYSIS_ALREADY_RUNNING",
                message="El preanalisis actual no puede reintentarse desde su estado actual.",
            )
        self._audit(
            PRE_ANALYSIS_EVENTS["retry_requested"],
            actor=user,
            case=case,
            pre_analysis=previous,
            metadata={"reason": reason, "previousStatus": previous.status},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self.create_or_reuse(
            case_id,
            force_regenerate=self._is_admin(user),
            source="retry_requested",
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def run(
        self,
        pre_analysis_id: str,
        *,
        actor: User | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        pre_analysis = self._get_pre_analysis_or_404(pre_analysis_id)
        case = self._get_case_or_404(pre_analysis.case_id)
        job = self.pre_analysis.latest_job(pre_analysis.id)
        if job is None:
            job = self.pre_analysis.create_job(
                pre_analysis_id=pre_analysis.id,
                case_id=pre_analysis.case_id,
                status="queued",
                attempts=0,
                max_attempts=settings.pre_analysis_job_max_attempts,
                progress=Decimal("0.00"),
                current_step="Esperando procesamiento",
            )
        if pre_analysis.status == "completed":
            return {"preAnalysisId": str(pre_analysis.id), "status": pre_analysis.status}
        if job.attempts >= job.max_attempts and job.status == "failed":
            return {"preAnalysisId": str(pre_analysis.id), "status": pre_analysis.status}

        inputs = self.validate_can_run(case=case, actor=actor, ip_address=ip_address, user_agent=user_agent)
        previous_status = pre_analysis.status
        now = utc_now()
        job.status = "running"
        job.attempts += 1
        job.started_at = now
        job.progress = Decimal("10.00")
        job.current_step = "Preparando insumos seguros"
        pre_analysis.status = "in_progress"
        pre_analysis.started_at = pre_analysis.started_at or now
        pre_analysis.updated_at = now
        self._audit(
            PRE_ANALYSIS_EVENTS["started"],
            actor=actor,
            case=case,
            pre_analysis=pre_analysis,
            previous_status=previous_status,
            new_status=pre_analysis.status,
            metadata={"jobId": str(job.id), "attempt": job.attempts},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        try:
            ai_input = self.build_ai_input(case=case, documents=inputs["documents"])
            job.progress = Decimal("35.00")
            job.current_step = "Detectando senales preliminares"
            provider = pre_analysis_provider_factory()
            result = provider.generate_structured_pre_analysis(ai_input)
            pre_analysis.ai_provider = result.provider
            pre_analysis.ai_model = result.model
            job.progress = Decimal("70.00")
            job.current_step = "Validando resultado preliminar"
            sanitized = self.apply_visibility_rules(result.output.model_dump(by_alias=True))
            self.persist_result(pre_analysis, sanitized)
            job.progress = Decimal("90.00")
            next_status = (
                "requires_review"
                if _decimal(sanitized["confidence"]) < LOW_CONFIDENCE_THRESHOLD
                else "completed"
            )
            previous_status = pre_analysis.status
            pre_analysis.status = next_status
            pre_analysis.cta_type = "wait_review" if next_status == "requires_review" else sanitized["ctaType"]
            pre_analysis.completed_at = utc_now()
            pre_analysis.updated_at = pre_analysis.completed_at
            job.status = "completed"
            job.progress = Decimal("100.00")
            job.current_step = "Preanalisis finalizado"
            job.finished_at = pre_analysis.completed_at
            self._transition_case(
                case,
                "requires_review" if next_status == "requires_review" else "preanalysis_ready",
                reason="Preanalisis preliminar finalizado.",
            )
            self._record_history_event(
                case=case,
                event_type=PRE_ANALYSIS_EVENTS[next_status],
                title="Preanalisis preliminar disponible",
                description="El resultado preliminar ya puede consultarse.",
                severity="warning" if next_status == "requires_review" else "success",
                actor=actor,
                metadata={"preAnalysisId": str(pre_analysis.id)},
            )
            self._audit(
                PRE_ANALYSIS_EVENTS["updated"],
                actor=actor,
                case=case,
                pre_analysis=pre_analysis,
                previous_status=previous_status,
                new_status=pre_analysis.status,
                metadata={"provider": result.provider, "model": result.model},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            terminal_event = "requires_review" if next_status == "requires_review" else "completed"
            self._audit(
                PRE_ANALYSIS_EVENTS[terminal_event],
                actor=actor,
                case=case,
                pre_analysis=pre_analysis,
                previous_status=previous_status,
                new_status=pre_analysis.status,
                metadata={"confidence": sanitized["confidence"], "jobId": str(job.id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"preAnalysisId": str(pre_analysis.id), "status": pre_analysis.status}
        except ApiError:
            self.db.rollback()
            raise
        except (PreAnalysisProviderError, PreAnalysisInvalidResponseError) as exc:
            self._mark_failed(
                pre_analysis,
                job=job,
                case=case,
                actor=actor,
                code=getattr(exc, "code", "PRE_ANALYSIS_PROVIDER_FAILED"),
                message=str(exc) or "No fue posible completar el preanalisis.",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"preAnalysisId": str(pre_analysis.id), "status": pre_analysis.status}
        except Exception as exc:
            self._mark_failed(
                pre_analysis,
                job=job,
                case=case,
                actor=actor,
                code="PRE_ANALYSIS_INTERNAL_ERROR",
                message="No fue posible completar el preanalisis.",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="PRE_ANALYSIS_PROVIDER_FAILED",
                message="No fue posible completar el preanalisis.",
            ) from exc

    def list_admin(
        self,
        *,
        status_filter: str | None,
        case_id: str | None,
        user_id: str | None,
        page: int,
        page_size: int,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_review_role(user)
        parsed_case_id = _parse_uuid(case_id)
        parsed_user_id = _parse_uuid(user_id)
        if case_id and parsed_case_id is None:
            raise self._invalid_uuid()
        if user_id and parsed_user_id is None:
            raise self._invalid_uuid()
        items, total = self.pre_analysis.list_admin(
            status_filter=status_filter,
            case_id=parsed_case_id,
            user_id=parsed_user_id,
            page=page,
            page_size=page_size,
        )
        self.audit_events.create(
            event_type=PRE_ANALYSIS_EVENTS["viewed"],
            entity_type="pre_analysis",
            actor_user_id=user.id,
            metadata={
                "actorRole": self._actor_role(user),
                "sourceModule": "preanalysis",
                "adminList": True,
                "status": status_filter,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "items": [self._admin_item(item) for item in items],
            "pagination": {"page": page, "pageSize": page_size, "total": total},
        }

    def review(
        self,
        pre_analysis_id: str,
        *,
        review_status: str,
        review_notes: str | None,
        traffic_light: str | None,
        viability_level: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_review_role(user)
        pre_analysis = self._get_pre_analysis_or_404(pre_analysis_id)
        case = self._get_case_or_404(pre_analysis.case_id)
        previous_status = pre_analysis.status
        previous_state = self._pre_analysis_state(pre_analysis)
        pre_analysis.status = review_status
        pre_analysis.review_notes = review_notes
        pre_analysis.reviewed_by = user.id
        pre_analysis.reviewed_at = utc_now()
        if traffic_light:
            pre_analysis.traffic_light = traffic_light
        if viability_level:
            pre_analysis.viability_level = viability_level
        if pre_analysis.viability is not None:
            if traffic_light:
                pre_analysis.viability.traffic_light = traffic_light
            if viability_level:
                pre_analysis.viability.level = viability_level
            pre_analysis.viability.updated_at = utc_now()
        if review_status == "completed":
            pre_analysis.completed_at = pre_analysis.completed_at or utc_now()
            self._transition_case(case, "preanalysis_ready", reason="Preanalisis revisado por operador.")
        pre_analysis.updated_at = utc_now()
        self._audit(
            PRE_ANALYSIS_EVENTS["reviewed"],
            actor=user,
            case=case,
            pre_analysis=pre_analysis,
            previous_status=previous_status,
            new_status=pre_analysis.status,
            previous_state=previous_state,
            new_state=self._pre_analysis_state(pre_analysis),
            metadata={"reviewNotes": bool(review_notes)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(pre_analysis)
        return self._user_result_payload(pre_analysis)

    def validate_can_run(
        self,
        *,
        case: LaboraCase,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if case.status in LOCKED_CASE_STATUSES:
            self._raise_blocked(
                case=case,
                actor=actor,
                blocked_reason="case_not_found",
                message="El expediente no permite iniciar preanalisis.",
                ip_address=ip_address,
                user_agent=user_agent,
            )
        permission = ConsentComplianceService(self.db).can_upload_documents(case.owner_user_id)
        if not permission.allowed:
            self._raise_blocked(
                case=case,
                actor=actor,
                blocked_reason="missing_consent",
                message="No podemos iniciar el preanalisis porque falta informacion obligatoria.",
                details={"missingConsentTypes": permission.missing_consent_types},
                ip_address=ip_address,
                user_agent=user_agent,
            )

        documents = self._main_documents(case)
        if not documents:
            self._raise_blocked(
                case=case,
                actor=actor,
                blocked_reason="missing_main_document",
                message="No podemos iniciar el preanalisis porque falta un documento principal.",
                ip_address=ip_address,
                user_agent=user_agent,
            )
        if not self._has_compatible_document_validation(documents):
            self._raise_blocked(
                case=case,
                actor=actor,
                blocked_reason="document_rejected",
                message="No podemos iniciar el preanalisis porque la validacion documental no es compatible.",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        extraction_run = self.extractions.latest_run(case.id)
        if not self._has_minimum_extraction(case=case, documents=documents, run=extraction_run):
            self._raise_blocked(
                case=case,
                actor=actor,
                blocked_reason="extraction_not_ready",
                message="No podemos iniciar el preanalisis porque la extraccion basica no esta lista.",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        if self._questionnaire_required(case) and not self._has_completed_questionnaire(case.id):
            self._raise_blocked(
                case=case,
                actor=actor,
                blocked_reason="questionnaire_required",
                message="No podemos iniciar el preanalisis porque falta el cuestionario minimo.",
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return {"documents": documents, "extractionRun": extraction_run}

    def build_ai_input(self, *, case: LaboraCase, documents: list[Document]) -> dict[str, Any]:
        document_types = sorted(
            {
                document.document_type.code
                for document in documents
                if document.document_type is not None
            }
        )
        page_count = sum(document.page_count or len(document.pages or []) for document in documents)
        validations = [
            self.documents.latest_validation(document.id)
            for document in documents
        ]
        warnings: list[str] = []
        quality_status = "accepted"
        for validation in validations:
            if validation is None:
                continue
            if validation.result == "accepted_with_warnings":
                quality_status = "accepted_with_warnings"
            for warning in validation.warnings or []:
                code = warning.get("code") if isinstance(warning, dict) else str(warning)
                if code:
                    warnings.append(str(code))
        periods_count = self._count(LaborPeriod, case.id)
        employers_count = self._count(Employer, case.id)
        gaps_count = self._count(ContributionGap, case.id)
        salary_count = self._count(SalaryBase, case.id)
        possible_regime_signals = self._possible_regime_signals(case.id)
        profile = self._case_profile(case.id)
        questionnaire_summary = {
            "isPensioned": case.situation_type in {"pensioned_with_doubts", "recognized_with_possible_error", "reliquidation_needed"},
            "hasPriorClaim": bool(profile.has_prior_claim) if profile else case.situation_type == "request_denied",
            "workedInPublicSector": bool(profile.has_public_sector_work) if profile else False,
            "wasTeacher": bool(profile.has_teacher_history) if profile else False,
        }
        return {
            "caseId": str(case.id),
            "documentSummary": {
                "detectedDocumentTypes": document_types or ["historia_laboral"],
                "qualityStatus": quality_status,
                "pageCount": page_count,
                "warnings": sorted(set(warnings)),
            },
            "extractionSummary": {
                "periodsCount": periods_count,
                "employersCount": employers_count,
                "hasGaps": gaps_count > 0,
                "hasSalaryData": salary_count > 0,
                "possibleRegimeSignals": possible_regime_signals,
            },
            "questionnaireSummary": questionnaire_summary,
        }

    def apply_visibility_rules(self, output: dict[str, Any]) -> dict[str, Any]:
        self._assert_no_prohibited_keys(output)
        sanitized = _sanitize_payload(output)
        issues = [
            {
                "type": _allowed_issue_type(item.get("type")),
                "severity": _allowed_severity(item.get("severity")),
                "title": _safe_text(item.get("title") or "Hallazgo preliminar", limit=180),
                "publicSummary": _safe_text(item.get("publicSummary") or "", limit=500),
                "lockedDetailAvailable": True,
                "evidenceRefs": item.get("evidenceRefs") or [],
                "confidence": _decimal_or_none(item.get("confidence")),
            }
            for item in sanitized.get("issues", [])[:5]
            if isinstance(item, dict)
        ]
        missing_documents = [
            {
                "documentType": _safe_code(item.get("documentType") or "soporte_adicional", limit=80),
                "title": _safe_text(item.get("title") or "Soporte adicional", limit=180),
                "priority": _allowed_priority(item.get("priority")),
                "reason": _safe_text(item.get("reason"), limit=500) if item.get("reason") else None,
                "status": "pending",
                "uploadHint": _safe_text(item.get("uploadHint"), limit=500) if item.get("uploadHint") else None,
            }
            for item in sanitized.get("missingDocuments", [])[:8]
            if isinstance(item, dict)
        ]
        case_signals = [
            {
                "signalType": _safe_code(item.get("signalType") or "preliminary_signal", limit=80),
                "title": _safe_text(item.get("title") or "Senal preliminar", limit=180),
                "publicSummary": _safe_text(item.get("publicSummary") or "", limit=500),
                "confidence": _decimal_or_none(item.get("confidence")),
                "source": _allowed_source(item.get("source")),
                "sourceRefs": item.get("sourceRefs") or [],
                "isVisibleToUser": True,
            }
            for item in sanitized.get("caseSignals", [])[:10]
            if isinstance(item, dict)
        ]
        confidence = _decimal(sanitized.get("confidence"))
        completion_score = _completion_score(sanitized.get("completionScore"))
        traffic_light = _allowed_traffic_light(sanitized.get("trafficLight"))
        viability_level = _allowed_viability_level(sanitized.get("viabilityLevel"))
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            traffic_light = "gray"
            viability_level = "insufficient"
        cta_type = "upload_missing_docs" if viability_level == "insufficient" and missing_documents else "unlock_full_analysis"
        return {
            "preliminaryCaseType": _allowed_preliminary_type(sanitized.get("preliminaryCaseType")),
            "trafficLight": traffic_light,
            "viabilityLevel": viability_level,
            "completionScore": completion_score,
            "confidence": confidence,
            "limitedSummary": _safe_text(sanitized.get("limitedSummary"), limit=700)
            or "Encontramos senales preliminares que podrian justificar una revision completa.",
            "valueDetectedTitle": _safe_text(sanitized.get("valueDetectedTitle"), limit=180)
            or "Hay senales que merecen revision",
            "valueDetectedSummary": _safe_text(sanitized.get("valueDetectedSummary"), limit=700)
            or "Se detectaron hallazgos generales, sin que esto constituya una conclusion final.",
            "issues": issues,
            "missingDocuments": missing_documents,
            "caseSignals": case_signals,
            "viability": {
                "level": viability_level,
                "trafficLight": traffic_light,
                "shortReason": _viability_reason(viability_level, len(issues), len(case_signals)),
                "publicRecommendation": _viability_recommendation(viability_level),
                "confidence": confidence,
            },
            "ctaType": cta_type,
        }

    def persist_result(self, pre_analysis: PreAnalysis, sanitized: dict[str, Any]) -> None:
        pre_analysis.preliminary_case_type = sanitized["preliminaryCaseType"]
        pre_analysis.traffic_light = sanitized["trafficLight"]
        pre_analysis.viability_level = sanitized["viabilityLevel"]
        pre_analysis.completion_score = sanitized["completionScore"]
        pre_analysis.confidence = sanitized["confidence"]
        pre_analysis.limited_summary = sanitized["limitedSummary"]
        pre_analysis.value_detected_title = sanitized["valueDetectedTitle"]
        pre_analysis.value_detected_summary = sanitized["valueDetectedSummary"]
        pre_analysis.cta_type = sanitized["ctaType"]
        self.pre_analysis.replace_result_rows(
            pre_analysis_id=pre_analysis.id,
            case_id=pre_analysis.case_id,
            issues=sanitized["issues"],
            viability=sanitized["viability"],
            missing_documents=sanitized["missingDocuments"],
            case_signals=sanitized["caseSignals"],
        )

    def _main_documents(self, case: LaboraCase) -> list[Document]:
        documents = [
            document
            for document in self.documents.list_active_case_documents(case.id)
            if document.status in READY_DOCUMENT_STATUSES and not document.is_duplicate
        ]
        main_documents = [
            document
            for document in documents
            if document.is_primary
            or (
                document.document_type is not None
                and (
                    document.document_type.code == "historia_laboral"
                    or document.document_type.is_primary_candidate
                )
            )
        ]
        return main_documents or documents[:1]

    def _has_compatible_document_validation(self, documents: list[Document]) -> bool:
        for document in documents:
            if document.status == "rejected" or document.validation_status == "rejected":
                continue
            validation = self.documents.latest_validation(document.id)
            if validation is None:
                if document.validation_status in {"accepted", "accepted_with_warnings", "completed"}:
                    return True
                if document.status in {"uploaded", "validated"}:
                    return True
                continue
            if validation.result in COMPATIBLE_VALIDATION_RESULTS:
                return True
        return False

    def _has_minimum_extraction(
        self,
        *,
        case: LaboraCase,
        documents: list[Document],
        run: ExtractionRun | None,
    ) -> bool:
        if run is not None and run.status in READY_EXTRACTION_STATUSES:
            return True
        if self._count(ExtractionField, case.id) > 0 or self._count(LaborPeriod, case.id) > 0:
            return True
        return any(
            any((page.text_extracted or "").strip() for page in document.pages[:3])
            for document in documents
        )

    def _questionnaire_required(self, case: LaboraCase) -> bool:
        return case.case_type_requested == "not_sure" or case.situation_type == "not_sure"

    def _has_completed_questionnaire(self, case_id: uuid.UUID) -> bool:
        if self._case_profile(case_id) is not None:
            return True
        session = (
            self.db.query(CaseQuestionnaireSession)
            .filter(CaseQuestionnaireSession.case_id == case_id)
            .order_by(CaseQuestionnaireSession.created_at.desc())
            .first()
        )
        if session is None:
            return False
        return session.status in {"completed", "submitted"} or float(session.completion_percentage or 0) >= 100

    def _input_hash(
        self,
        *,
        case: LaboraCase,
        documents: list[Document],
        extraction_run: ExtractionRun | None,
    ) -> str:
        payload = {
            "case": {
                "id": str(case.id),
                "caseTypeRequested": case.case_type_requested,
                "situationType": case.situation_type,
                "holderBirthDate": case.holder_birth_date,
                "pensionFundOrEntity": case.pension_fund_or_entity,
            },
            "documents": [
                {
                    "id": str(document.id),
                    "sha256": document.sha256_hash,
                    "status": document.status,
                    "validationStatus": document.validation_status,
                    "updatedAt": document.updated_at,
                    "pageCount": document.page_count,
                }
                for document in sorted(documents, key=lambda item: str(item.id))
            ],
            "extractionRun": {
                "id": str(extraction_run.id) if extraction_run else None,
                "status": extraction_run.status if extraction_run else None,
                "confirmationStatus": extraction_run.confirmation_status if extraction_run else None,
                "updatedAt": extraction_run.updated_at if extraction_run else None,
            },
            "questionnaire": self._questionnaire_hash_fragment(case.id),
            "version": OUTPUT_SCHEMA_VERSION,
        }
        return hashlib.sha256(
            json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ).hexdigest()

    def _questionnaire_hash_fragment(self, case_id: uuid.UUID) -> dict[str, Any]:
        session = (
            self.db.query(CaseQuestionnaireSession)
            .filter(CaseQuestionnaireSession.case_id == case_id)
            .order_by(CaseQuestionnaireSession.updated_at.desc())
            .first()
        )
        return {
            "sessionId": str(session.id) if session else None,
            "status": session.status if session else None,
            "updatedAt": session.updated_at if session else None,
        }

    def _possible_regime_signals(self, case_id: uuid.UUID) -> list[str]:
        signals: set[str] = set()
        profile = self._case_profile(case_id)
        if profile is not None:
            if profile.has_public_sector_work:
                signals.add("servidor_publico_posible")
            if profile.has_teacher_history:
                signals.add("docente_magisterio_posible")
            if profile.has_special_regime_signal:
                signals.add("regimen_especial_posible")
        for period in self.db.query(LaborPeriod.regime_hint).filter(LaborPeriod.case_id == case_id).all():
            hint = period[0]
            if hint == "public":
                signals.add("servidor_publico_posible")
            if hint == "teacher":
                signals.add("docente_magisterio_posible")
            if hint == "special":
                signals.add("regimen_especial_posible")
        for employer in self.db.query(Employer.employer_type).filter(Employer.case_id == case_id).all():
            value = employer[0]
            if value == "public":
                signals.add("servidor_publico_posible")
            if value == "teacher":
                signals.add("docente_magisterio_posible")
        return sorted(signals)

    def _count(self, model, case_id: uuid.UUID) -> int:
        query = self.db.query(func.count(model.id)).filter(model.case_id == case_id)
        if hasattr(model, "status"):
            query = query.filter(model.status != "ignored")
        return query.scalar() or 0

    def _case_profile(self, case_id: uuid.UUID) -> CaseProfile | None:
        return self.db.query(CaseProfile).filter(CaseProfile.case_id == case_id).one_or_none()

    def _user_result_payload(self, pre_analysis: PreAnalysis) -> dict[str, Any]:
        warnings = [
            {
                "code": "PRELIMINARY_ONLY",
                "message": "Este resultado es preliminar y no reemplaza el analisis completo.",
            }
        ]
        if pre_analysis.status == "requires_review" or (
            pre_analysis.confidence is not None and Decimal(pre_analysis.confidence) < LOW_CONFIDENCE_THRESHOLD
        ):
            warnings.append(
                {
                    "code": "LOW_CONFIDENCE_REVIEW",
                    "message": "El resultado requiere revision por baja confianza.",
                }
            )
        return {
            "id": str(pre_analysis.id),
            "caseId": str(pre_analysis.case_id),
            "status": pre_analysis.status,
            "blockedReason": pre_analysis.blocked_reason,
            "trafficLight": pre_analysis.traffic_light,
            "viabilityLevel": pre_analysis.viability_level,
            "completionScore": _float(pre_analysis.completion_score),
            "confidence": _float(pre_analysis.confidence),
            "preliminaryCaseType": pre_analysis.preliminary_case_type,
            "limitedSummary": pre_analysis.limited_summary,
            "valueDetected": {
                "title": pre_analysis.value_detected_title,
                "summary": pre_analysis.value_detected_summary,
            },
            "issues": [self._issue_payload(issue) for issue in self.pre_analysis.list_visible_issues(pre_analysis.id)],
            "missingDocuments": [
                self._missing_document_payload(item)
                for item in self.pre_analysis.list_missing_documents(pre_analysis.id)
            ],
            "cta": self._cta(pre_analysis.cta_type),
            "warnings": warnings,
            "createdAt": pre_analysis.created_at,
            "completedAt": pre_analysis.completed_at,
        }

    def _not_started_payload(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": None,
            "caseId": str(case.id),
            "status": "not_started",
            "valueDetected": {"title": None, "summary": None},
            "issues": [],
            "missingDocuments": [],
            "cta": None,
            "warnings": [],
            "createdAt": None,
            "completedAt": None,
        }

    def _issue_payload(self, issue) -> dict[str, Any]:
        return {
            "id": str(issue.id),
            "type": issue.issue_type,
            "severity": issue.severity,
            "title": issue.title,
            "publicSummary": issue.public_summary,
            "lockedDetailAvailable": issue.locked_detail_available,
            "confidence": _float(issue.confidence),
        }

    def _missing_document_payload(self, item) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "documentType": item.document_type,
            "title": item.title,
            "priority": item.priority,
            "reason": item.reason,
            "status": item.status,
            "uploadHint": item.upload_hint,
        }

    def _cta(self, cta_type: str | None) -> dict[str, str] | None:
        if not cta_type:
            return None
        copy = CTA_COPY.get(cta_type, CTA_COPY["unlock_full_analysis"])
        return {"type": cta_type, **copy}

    def _admin_item(self, item: PreAnalysis) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "userId": str(item.user_id),
            "status": item.status,
            "trafficLight": item.traffic_light,
            "viabilityLevel": item.viability_level,
            "confidence": _float(item.confidence),
            "createdAt": item.created_at,
            "completedAt": item.completed_at,
        }

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_pre_analysis_or_404(self, pre_analysis_id: str | uuid.UUID) -> PreAnalysis:
        item = self.pre_analysis.get(pre_analysis_id)
        if item is None or item.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="PRE_ANALYSIS_NOT_FOUND",
                message="Preanalisis no encontrado.",
            )
        return item

    def _require_can_view(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_view(case, user):
            return
        self._audit_access_denied(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)

    def _require_can_update(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._can_update(case, user):
            return
        self._audit_access_denied(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return case.status == "requires_review" or self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"legal_reviewer"},
            ) is not None
        if case.owner_user_id == user.id:
            return True
        return self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        ) is not None

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if self._is_admin(user):
            return True
        if user.role in LEGAL_REVIEWER_ROLES:
            return False
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _require_review_role(self, user: User) -> None:
        if user.role not in PRE_ANALYSIS_REVIEW_ROLES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="PERMISSION_DENIED",
                message="No tienes permisos para revisar preanalisis.",
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
            "analisis_preliminar_gratuito.access_denied",
            actor=actor,
            case=case,
            pre_analysis=None,
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="PERMISSION_DENIED",
            message="No tienes permisos para acceder a este expediente.",
            details={"blockedReason": "permission_denied"},
        )

    def _raise_blocked(
        self,
        *,
        case: LaboraCase,
        actor: User | None,
        blocked_reason: str,
        message: str,
        ip_address: str | None,
        user_agent: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._audit(
            PRE_ANALYSIS_EVENTS["blocked"],
            actor=actor,
            case=case,
            pre_analysis=None,
            new_status="blocked",
            metadata={"blockedReason": blocked_reason, **(details or {})},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="PRE_ANALYSIS_BLOCKED",
            message=message,
            details={"blockedReason": blocked_reason, **(details or {})},
        )

    def _mark_failed(
        self,
        pre_analysis: PreAnalysis,
        *,
        job: PreAnalysisJob,
        case: LaboraCase,
        actor: User | None,
        code: str,
        message: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        previous_status = pre_analysis.status
        pre_analysis.status = "error"
        pre_analysis.updated_at = utc_now()
        job.status = "failed"
        job.error_code = code
        job.error_message = message
        job.finished_at = utc_now()
        job.current_step = "No fue posible completar el preanalisis"
        self._transition_case(case, "error", reason="Fallo el preanalisis preliminar.")
        self._audit(
            PRE_ANALYSIS_EVENTS["failed"],
            actor=actor,
            case=case,
            pre_analysis=pre_analysis,
            previous_status=previous_status,
            new_status=pre_analysis.status,
            metadata={"jobId": str(job.id), "errorCode": code},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _transition_case(self, case: LaboraCase, new_status: str, *, reason: str) -> None:
        if case.status == new_status or case.status in LOCKED_CASE_STATUSES:
            return
        case.status = new_status
        case.status_reason = reason
        case.current_step, case.next_best_action = step_for_status(new_status)
        case.updated_at = utc_now()

    def _record_history_event(
        self,
        *,
        case: LaboraCase,
        event_type: str,
        title: str,
        description: str | None,
        severity: str,
        actor: User | None,
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
                created_by_user_id=actor.id if actor else None,
                metadata_json=_json_safe(metadata) if metadata else None,
            )
        )
        self.db.flush()

    def _audit(
        self,
        event_name: str,
        *,
        actor: User | None,
        case: LaboraCase,
        pre_analysis: PreAnalysis | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_status: str | None = None,
        new_status: str | None = None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "preAnalysisId": str(pre_analysis.id) if pre_analysis else None,
            "actorRole": self._actor_role(actor),
            "previousStatus": previous_status,
            "newStatus": new_status,
            "sourceModule": "preanalysis",
        }
        if metadata:
            event_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_name,
            entity_type="pre_analysis",
            entity_id=pre_analysis.id if pre_analysis else None,
            actor_user_id=actor.id if actor else None,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
            metadata=_json_safe(event_metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _pre_analysis_state(self, item: PreAnalysis) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "status": item.status,
            "trafficLight": item.traffic_light,
            "viabilityLevel": item.viability_level,
            "confidence": item.confidence,
            "completionScore": item.completion_score,
        }

    def _assert_no_prohibited_keys(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in PROHIBITED_KEYS:
                    raise ApiError(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        code="PRE_ANALYSIS_SCHEMA_INVALID",
                        message="La respuesta IA contiene campos no permitidos para preanalisis.",
                    )
                self._assert_no_prohibited_keys(item)
        elif isinstance(value, list):
            for item in value:
                self._assert_no_prohibited_keys(item)

    def _actor_role(self, user: User | None) -> str:
        if user is None:
            return "system"
        if self._is_admin(user):
            return "admin"
        if user.role in LEGAL_REVIEWER_ROLES:
            return "legal_reviewer"
        if user.role == "system":
            return "system"
        return "user"

    def _is_admin(self, user: User) -> bool:
        return user.role in ADMIN_ROLES

    def _invalid_uuid(self) -> ApiError:
        return ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="PRE_ANALYSIS_VALIDATION_ERROR",
            message="Identificador UUID invalido.",
        )


def _progress(pre_analysis: PreAnalysis, job: PreAnalysisJob | None) -> int:
    if job is not None:
        return int(job.progress or 0)
    if pre_analysis.status == "completed":
        return 100
    if pre_analysis.status == "requires_review":
        return 90
    if pre_analysis.status == "error":
        return 100
    return 0


def _current_step(pre_analysis: PreAnalysis, job: PreAnalysisJob | None) -> str:
    if job is not None and job.current_step:
        return job.current_step
    return {
        "queued": "Esperando procesamiento",
        "in_progress": "Detectando senales preliminares",
        "completed": "Preanalisis finalizado",
        "requires_review": "Pendiente de revision",
        "blocked": "Bloqueado por insumos faltantes",
        "error": "No fue posible completar el preanalisis",
    }.get(pre_analysis.status, "Sin iniciar")


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value, limit=2000)
    return value


def _safe_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).strip().split())
    if not text:
        return ""
    if any(pattern.search(text) for pattern in PROHIBITED_TEXT_PATTERNS):
        return "Detalle reservado para el analisis completo."
    return text[:limit]


def _safe_code(value: Any, *, limit: int) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "unknown")[:limit]


def _allowed_preliminary_type(value: Any) -> str:
    allowed = {
        "historia_laboral",
        "reliquidacion_pensional",
        "semanas_no_reconocidas",
        "mora_patronal",
        "omision_afiliacion",
        "regimen_transicion_posible",
        "docente_magisterio_posible",
        "servidor_publico_posible",
        "regimen_especial_posible",
        "informacion_insuficiente",
    }
    value = _safe_code(value, limit=64)
    return value if value in allowed else "informacion_insuficiente"


def _allowed_issue_type(value: Any) -> str:
    allowed = {
        "missing_periods",
        "possible_unrecognized_weeks",
        "possible_salary_inconsistency",
        "possible_regime_mismatch",
        "missing_documents",
        "document_quality_warning",
        "questionnaire_conflict",
        "possible_public_service_time",
        "possible_teacher_regime",
        "possible_employer_default",
        "insufficient_information",
    }
    value = _safe_code(value, limit=64)
    return value if value in allowed else "insufficient_information"


def _allowed_severity(value: Any) -> str:
    return str(value) if value in {"low", "medium", "high"} else "medium"


def _allowed_priority(value: Any) -> str:
    return str(value) if value in {"required", "recommended", "optional"} else "recommended"


def _allowed_source(value: Any) -> str:
    return str(value) if value in {"extraction", "questionnaire", "document_validation", "ai", "rules"} else "rules"


def _allowed_traffic_light(value: Any) -> str:
    return str(value) if value in {"green", "yellow", "red", "gray"} else "gray"


def _allowed_viability_level(value: Any) -> str:
    return str(value) if value in {"high", "medium", "low", "insufficient"} else "insufficient"


def _completion_score(value: Any) -> Decimal:
    parsed = Decimal(str(value if value is not None else 0))
    if parsed < 0:
        parsed = Decimal("0")
    if parsed > 100:
        parsed = Decimal("100")
    return parsed.quantize(Decimal("0.01"))


def _decimal(value: Any) -> Decimal:
    parsed = Decimal(str(value if value is not None else 0))
    if parsed < 0:
        parsed = Decimal("0")
    if parsed > 1:
        parsed = Decimal("1")
    return parsed.quantize(Decimal("0.0001"))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _viability_reason(level: str, issues_count: int, signals_count: int) -> str:
    if level == "high":
        return "Hay senales preliminares fuertes y suficientes soportes iniciales."
    if level == "medium":
        return f"Hay {signals_count} senales preliminares y {issues_count} hallazgos generales por revisar."
    if level == "low":
        return "Hay senales iniciales, pero la informacion disponible todavia es limitada."
    return "La informacion disponible no permite una orientacion preliminar suficiente."


def _viability_recommendation(level: str) -> str:
    if level in {"high", "medium"}:
        return "Desbloquear el analisis completo permitiria validar calculo, fundamentos y reporte tecnico."
    return "Conviene completar soportes o esperar revision antes de avanzar."


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


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
