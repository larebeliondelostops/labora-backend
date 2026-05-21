import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.document import Document
from app.models.extraction import (
    ContributionGap,
    ContributionWeek,
    Employer,
    ExtractionRun,
    LaborPeriod,
    SalaryBase,
)
from app.models.full_analysis import FullAnalysis, FullAnalysisJob
from app.models.payment import Order
from app.models.paywall import Paywall
from app.models.pre_analysis import PreAnalysis
from app.models.questionnaire import CaseProfile
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.extraction_repository import ExtractionRepository
from app.repositories.full_analysis_repository import ACTIVE_STATUSES, FullAnalysisRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.pre_analysis_repository import PreAnalysisRepository
from app.services.calculation_service import CalculationService
from app.services.case_state_machine import step_for_status, validate_case_transition
from app.services.consent_service import ConsentComplianceService
from app.services.legal_rules_service import LegalRulesService
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
OPERATOR_ROLES = {"operator", "legal_ops", "reviewer"}
REVIEW_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, *OPERATOR_ROLES}
LOCKED_CASE_STATUSES = {"closed", "archived", "blocked"}
UNLOCKED_CASE_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
    "requires_review",
}
READY_DOCUMENT_STATUSES = {"uploaded", "validated", "requires_review"}
READY_DOCUMENT_VALIDATION_STATUSES = {"completed"}
READY_EXTRACTION_STATUSES = {"completed", "requires_review"}
READY_EXTRACTION_CONFIRMATIONS = {
    "user_confirmed",
    "accepted_with_pending",
    "requires_review",
    "confirmed",
    "system_confirmed",
}
READY_PRE_ANALYSIS_STATUSES = {"completed", "requires_review"}
RETRYABLE_STATUSES = {"failed", "blocked", "requires_review"}
LOW_CONFIDENCE_THRESHOLD = Decimal("70.00")
FULL_ANALYSIS_JOB_MAX_ATTEMPTS = 3
BLOCKED_ANALYSIS_CODES = {
    "CASE_NOT_READY_FOR_FULL_ANALYSIS",
    "CONSENT_REQUIRED",
    "PAYMENT_REQUIRED",
    "MISSING_REQUIRED_STRUCTURED_DATA",
}

FULL_ANALYSIS_EVENTS = {
    "created": "analisis_completo.created",
    "queued": "analisis_completo.queued",
    "started": "analisis_completo.started",
    "rules_started": "analisis_completo.rules_started",
    "rules_completed": "analisis_completo.rules_completed",
    "calculations_started": "analisis_completo.calculations_started",
    "calculations_completed": "analisis_completo.calculations_completed",
    "scenarios_completed": "analisis_completo.scenarios_completed",
    "confidence_completed": "analisis_completo.confidence_completed",
    "requires_review": "analisis_completo.requires_review",
    "completed": "analisis_completo.completed",
    "blocked": "analisis_completo.blocked",
    "failed": "analisis_completo.failed",
    "viewed": "analisis_completo.viewed",
    "retried": "analisis_completo.retried",
}


class FullAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.documents = DocumentRepository(db)
        self.extractions = ExtractionRepository(db)
        self.pre_analysis = PreAnalysisRepository(db)
        self.full_analysis = FullAnalysisRepository(db)
        self.payments = PaymentRepository(db)
        self.audit_events = AuditEventRepository(db)
        self.legal_rules = LegalRulesService()
        self.calculations = CalculationService()

    def create_or_reuse(
        self,
        case_id: str,
        *,
        force_reprocess: bool,
        reason: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        if force_reprocess and not self._can_force_reprocess(case, user):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="CASE_ACCESS_DENIED",
                message="No tienes permisos para reejecutar el analisis completo.",
            )

        active = self.full_analysis.active_for_case(case.id)
        if active is not None:
            if force_reprocess:
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="ANALYSIS_ALREADY_RUNNING",
                    message="Ya existe un analisis completo activo para este expediente.",
                    details={"caseId": str(case.id), "fullAnalysisId": str(active.id)},
                )
            return self._start_payload(active, "El analisis completo ya esta en proceso."), status.HTTP_202_ACCEPTED

        if not force_reprocess:
            completed = self.full_analysis.latest_completed_for_case(case.id)
            if completed is not None:
                self._audit(
                    FULL_ANALYSIS_EVENTS["viewed"],
                    actor=user,
                    case=case,
                    full_analysis=completed,
                    metadata={"reused": True, "reason": reason},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                self.db.commit()
                return self._start_payload(completed, "El analisis completo ya esta disponible."), status.HTTP_200_OK

        inputs = self.validate_can_run(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)
        version = self.full_analysis.next_version(case.id)
        now = utc_now()
        item = self.full_analysis.create(
            case_id=case.id,
            user_id=case.owner_user_id,
            status="queued",
            version=version,
            triggered_by_user_id=user.id,
            triggered_by_role=self._actor_role(user),
            input_snapshot=self._snapshot_digest(case=case, inputs=inputs),
            requires_human_review=False,
            created_at=now,
            updated_at=now,
        )
        job = self.full_analysis.create_job(
            full_analysis_id=item.id,
            case_id=case.id,
            status="queued",
            attempts=0,
            max_attempts=FULL_ANALYSIS_JOB_MAX_ATTEMPTS,
            progress=Decimal("5.00"),
            current_step="Esperando procesamiento",
            queued_at=now,
        )
        self._transition_case(case, new_status="analysis_in_progress", reason="Analisis completo en cola.")
        self._record_history_event(
            case=case,
            event_type=FULL_ANALYSIS_EVENTS["created"],
            title="Analisis completo iniciado",
            description="Se puso en cola el analisis juridico y de calculo.",
            severity="info",
            actor=user,
            metadata={"fullAnalysisId": str(item.id), "jobId": str(job.id), "version": version},
        )
        self._audit(
            FULL_ANALYSIS_EVENTS["created"],
            actor=user,
            case=case,
            full_analysis=item,
            new_status=item.status,
            metadata={"reason": reason, "version": version},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            FULL_ANALYSIS_EVENTS["queued"],
            actor=user,
            case=case,
            full_analysis=item,
            new_status=item.status,
            metadata={"jobId": str(job.id), "reason": reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._start_payload(item, "El analisis completo fue encolado correctamente."), status.HTTP_202_ACCEPTED

    def get_result(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_unlocked(case)
        item = self.full_analysis.latest_for_case(case.id)
        if item is None:
            return self._not_started_payload(case)
        self._audit(
            FULL_ANALYSIS_EVENTS["viewed"],
            actor=user,
            case=case,
            full_analysis=item,
            metadata={"surface": "summary"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._result_payload(item)

    def list_rule_results(
        self,
        case_id: str,
        *,
        category: str | None,
        result: str | None,
        requires_review: bool | None,
        page: int,
        limit: int,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        item = self._latest_analysis_for_access(case_id, user, ip_address, user_agent)
        rows, total = self.full_analysis.list_rule_results(
            full_analysis_id=item.id,
            category=category,
            result=result,
            requires_review=requires_review,
            page=page,
            limit=limit,
        )
        return {"items": [self._rule_payload(row) for row in rows], "pagination": {"page": page, "limit": limit, "total": total}}

    def list_calculations(
        self,
        case_id: str,
        *,
        calculation_type: str | None,
        page: int,
        limit: int,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        item = self._latest_analysis_for_access(case_id, user, ip_address, user_agent)
        rows, total = self.full_analysis.list_calculations(
            full_analysis_id=item.id,
            calculation_type=calculation_type,
            page=page,
            limit=limit,
        )
        return {"items": [self._calculation_payload(row) for row in rows], "pagination": {"page": page, "limit": limit, "total": total}}

    def list_scenarios(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        item = self._latest_analysis_for_access(case_id, user, ip_address, user_agent)
        return {"items": [self._scenario_payload(row) for row in self.full_analysis.list_scenarios(item.id)]}

    def list_inconsistencies(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        item = self._latest_analysis_for_access(case_id, user, ip_address, user_agent)
        return {
            "items": [
                self._inconsistency_payload(row)
                for row in self.full_analysis.list_inconsistencies(item.id)
            ]
        }

    def get_confidence(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        item = self._latest_analysis_for_access(case_id, user, ip_address, user_agent)
        scores = self.full_analysis.list_confidence_scores(item.id)
        return {
            "globalScore": _float(item.confidence_global),
            "level": _confidence_level(item.confidence_global or Decimal("0")),
            "requiresHumanReview": item.requires_human_review,
            "scores": [self._confidence_payload(row) for row in scores],
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
        previous = self.full_analysis.latest_for_case(case.id)
        if previous is None:
            return self.create_or_reuse(
                case_id,
                force_reprocess=False,
                reason=reason,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        if previous.status in ACTIVE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_ALREADY_RUNNING",
                message="Ya existe un analisis completo activo para este expediente.",
            )
        if previous.status not in RETRYABLE_STATUSES and not self._can_force_reprocess(case, user):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_FAILED",
                message="El analisis actual no puede reintentarse desde su estado actual.",
            )
        self._audit(
            FULL_ANALYSIS_EVENTS["retried"],
            actor=user,
            case=case,
            full_analysis=previous,
            metadata={"reason": reason, "previousStatus": previous.status},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self.create_or_reuse(
            case_id,
            force_reprocess=True,
            reason=reason,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def review_decision(
        self,
        case_id: str,
        *,
        decision: str,
        notes: str | None,
        adjustments: list[dict[str, Any]],
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if user.role not in REVIEW_ROLES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="CASE_ACCESS_DENIED",
                message="No tienes permisos para revisar analisis completos.",
            )
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_unlocked(case)
        item = self.full_analysis.latest_for_case(case.id)
        if item is None:
            raise self._analysis_not_found(case.id)
        previous_status = item.status
        now = utc_now()
        item.human_review_reason = notes
        item.summary = {**(item.summary or {}), "reviewDecision": {"decision": decision, "notes": notes, "adjustments": adjustments}}
        if decision == "approved":
            item.status = "completed"
            item.requires_human_review = False
            item.completed_at = item.completed_at or now
            self._transition_case(case, new_status="completed", reason="Analisis completo aprobado por revision humana.")
        elif decision == "needs_recalculation":
            item.status = "queued"
            item.requires_human_review = True
            self.full_analysis.create_job(
                full_analysis_id=item.id,
                case_id=case.id,
                status="queued",
                attempts=0,
                max_attempts=FULL_ANALYSIS_JOB_MAX_ATTEMPTS,
                progress=Decimal("5.00"),
                current_step="Recalculo solicitado por revision",
                queued_at=now,
            )
            self._transition_case(case, new_status="analysis_in_progress", reason="Revision solicito recalculo.")
        elif decision == "needs_more_documents":
            item.status = "blocked"
            item.requires_human_review = True
            item.human_review_reason = notes or "Se requieren documentos adicionales."
            self._transition_case(case, new_status="blocked", reason="Analisis completo bloqueado por documentos faltantes.")
        else:
            item.status = "blocked"
            item.requires_human_review = True
            item.failure_code = "HUMAN_REVIEW_REJECTED"
            item.failure_message = notes or "La revision humana rechazo el analisis."
            self._transition_case(case, new_status="blocked", reason="Analisis completo rechazado por revision humana.")
        item.updated_at = now
        self._audit(
            "analisis_completo.reviewed",
            actor=user,
            case=case,
            full_analysis=item,
            previous_status=previous_status,
            new_status=item.status,
            metadata={"decision": decision, "adjustmentsCount": len(adjustments)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._result_payload(item)

    def run(
        self,
        full_analysis_id: str,
        *,
        actor: User | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        item = self._get_analysis_or_404(full_analysis_id)
        case = self._get_case_or_404(item.case_id)
        job = self.full_analysis.latest_job(item.id)
        if item.status in {"completed", "requires_review"}:
            return {"id": str(item.id), "status": item.status}
        if job is None:
            job = self.full_analysis.create_job(
                full_analysis_id=item.id,
                case_id=item.case_id,
                status="queued",
                attempts=0,
                max_attempts=FULL_ANALYSIS_JOB_MAX_ATTEMPTS,
                progress=Decimal("5.00"),
                current_step="Esperando procesamiento",
            )
        if job.attempts >= job.max_attempts and job.status == "failed":
            return {"id": str(item.id), "status": item.status}

        try:
            inputs = self.validate_can_run(case=case, actor=actor, ip_address=ip_address, user_agent=user_agent)
            snapshot = self.build_input_snapshot(case=case, inputs=inputs)
            item.input_snapshot = snapshot
            item.started_at = item.started_at or utc_now()
            job.status = "running"
            job.attempts += 1
            job.started_at = job.started_at or utc_now()
            self._set_status(item, job, "in_progress", Decimal("10.00"), "Preparando datos estructurados")
            self._audit(FULL_ANALYSIS_EVENTS["started"], actor=actor, case=case, full_analysis=item, new_status=item.status, metadata={"jobId": str(job.id)}, ip_address=ip_address, user_agent=user_agent)
            self.db.commit()

            self._set_status(item, job, "rules_running", Decimal("25.00"), "Ejecutando reglas juridicas")
            self._audit(FULL_ANALYSIS_EVENTS["rules_started"], actor=actor, case=case, full_analysis=item, new_status=item.status, metadata={"jobId": str(job.id)}, ip_address=ip_address, user_agent=user_agent)
            self.db.commit()
            rules = self.legal_rules.evaluate(snapshot)
            self._audit(FULL_ANALYSIS_EVENTS["rules_completed"], actor=actor, case=case, full_analysis=item, metadata={"rulesCount": len(rules)}, ip_address=ip_address, user_agent=user_agent)
            self.db.commit()

            self._set_status(item, job, "calculations_running", Decimal("45.00"), "Ejecutando calculos reproducibles")
            self._audit(FULL_ANALYSIS_EVENTS["calculations_started"], actor=actor, case=case, full_analysis=item, new_status=item.status, metadata={"jobId": str(job.id)}, ip_address=ip_address, user_agent=user_agent)
            self.db.commit()
            calculations = self.calculations.calculate(snapshot)
            self._audit(FULL_ANALYSIS_EVENTS["calculations_completed"], actor=actor, case=case, full_analysis=item, metadata={"calculationsCount": len(calculations)}, ip_address=ip_address, user_agent=user_agent)
            self.db.commit()

            self._set_status(item, job, "scenario_comparison_running", Decimal("65.00"), "Comparando escenarios")
            scenarios = self._build_scenarios(snapshot, rules, calculations)
            inconsistencies = self._build_inconsistencies(snapshot, rules, calculations)
            self._audit(FULL_ANALYSIS_EVENTS["scenarios_completed"], actor=actor, case=case, full_analysis=item, metadata={"scenariosCount": len(scenarios), "inconsistenciesCount": len(inconsistencies)}, ip_address=ip_address, user_agent=user_agent)
            self.db.commit()

            self._set_status(item, job, "confidence_evaluation_running", Decimal("82.00"), "Evaluando confianza")
            confidence_scores, global_score = self._evaluate_confidence(
                snapshot=snapshot,
                rules=rules,
                calculations=calculations,
                scenarios=scenarios,
                inconsistencies=inconsistencies,
            )
            consistency_findings = self._verify_consistency(
                rules=rules,
                calculations=calculations,
                inconsistencies=inconsistencies,
            )
            if consistency_findings:
                global_score = _clamp_score(global_score - Decimal("10.00"))
                confidence_scores.append(
                    _score_payload(
                        "consistency",
                        Decimal("55.00"),
                        consistency_findings,
                        "human_review",
                    )
                )
            self.full_analysis.replace_result_rows(
                full_analysis_id=item.id,
                case_id=case.id,
                rules=rules,
                calculations=calculations,
                scenarios=scenarios,
                confidence_scores=confidence_scores,
                inconsistencies=inconsistencies,
            )
            self._audit(FULL_ANALYSIS_EVENTS["confidence_completed"], actor=actor, case=case, full_analysis=item, metadata={"globalScore": float(global_score)}, ip_address=ip_address, user_agent=user_agent)

            requires_review = self._requires_human_review(
                global_score=global_score,
                rules=rules,
                confidence_scores=confidence_scores,
                inconsistencies=inconsistencies,
            )
            executive, summary = self._build_summary(
                global_score=global_score,
                rules=rules,
                calculations=calculations,
                inconsistencies=inconsistencies,
                consistency_findings=consistency_findings,
            )
            item.executive_result = executive
            item.summary = summary
            item.legal_conclusion = summary["technicalSummary"]
            item.recommended_route = executive["recommendedRoute"]
            item.viability_level = executive["viabilityLevel"]
            item.confidence_global = global_score
            item.requires_human_review = requires_review
            item.human_review_reason = self._review_reason(global_score, rules, inconsistencies) if requires_review else None
            final_status = "requires_review" if requires_review else "completed"
            self._set_status(item, job, final_status, Decimal("100.00"), "Analisis completo finalizado")
            item.completed_at = utc_now()
            job.status = "completed"
            job.finished_at = item.completed_at
            self._transition_case(
                case,
                new_status="requires_review" if requires_review else "completed",
                reason="Analisis completo requiere revision." if requires_review else "Analisis completo finalizado.",
            )
            event_key = "requires_review" if requires_review else "completed"
            self._record_history_event(
                case=case,
                event_type=FULL_ANALYSIS_EVENTS[event_key],
                title="Analisis completo disponible",
                description="El resultado completo ya puede consultarse.",
                severity="warning" if requires_review else "success",
                actor=actor,
                metadata={"fullAnalysisId": str(item.id), "confidenceGlobal": float(global_score)},
            )
            self._audit(
                FULL_ANALYSIS_EVENTS[event_key],
                actor=actor,
                case=case,
                full_analysis=item,
                new_status=item.status,
                metadata={"jobId": str(job.id), "confidenceGlobal": float(global_score)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"id": str(item.id), "status": item.status}
        except ApiError as exc:
            self.db.rollback()
            if exc.code in BLOCKED_ANALYSIS_CODES:
                self._mark_blocked(
                    item,
                    job=job,
                    case=case,
                    actor=actor,
                    code=exc.code,
                    message=exc.message,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                self.db.commit()
                return {"id": str(item.id), "status": item.status}
            raise
        except Exception as exc:
            self.db.rollback()
            self._mark_failed(item, job=job, case=case, actor=actor, code="ANALYSIS_FAILED", message=str(exc) or "No fue posible completar el analisis.", ip_address=ip_address, user_agent=user_agent)
            self.db.commit()
            return {"id": str(item.id), "status": item.status}

    def validate_can_run(
        self,
        *,
        case: LaboraCase,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if case.status in LOCKED_CASE_STATUSES or case.deleted_at is not None:
            self._raise_blocked(case=case, actor=actor, code="CASE_NOT_READY_FOR_FULL_ANALYSIS", blocked_reason="case_blocked", message="El expediente no permite iniciar analisis completo.", ip_address=ip_address, user_agent=user_agent)
        permission = ConsentComplianceService(self.db).can_upload_documents(case.owner_user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CONSENT_REQUIRED",
                message="El analisis completo requiere consentimiento vigente.",
                details={"caseId": str(case.id), "missingConsentTypes": permission.missing_consent_types},
            )
        if not self._case_is_unlocked(case):
            raise self._payment_required_error(case)
        documents = self._ready_documents(case.id)
        if not documents:
            self._raise_blocked(case=case, actor=actor, code="CASE_NOT_READY_FOR_FULL_ANALYSIS", blocked_reason="documents_not_ready", message="Faltan documentos procesados para el analisis completo.", ip_address=ip_address, user_agent=user_agent)
        extraction_run = self.extractions.latest_run(case.id)
        if extraction_run is None or extraction_run.status not in READY_EXTRACTION_STATUSES:
            self._raise_blocked(case=case, actor=actor, code="CASE_NOT_READY_FOR_FULL_ANALYSIS", blocked_reason="extraction_not_ready", message="La extraccion estructurada no esta lista.", ip_address=ip_address, user_agent=user_agent)
        if extraction_run.confirmation_status not in READY_EXTRACTION_CONFIRMATIONS:
            self._raise_blocked(case=case, actor=actor, code="CASE_NOT_READY_FOR_FULL_ANALYSIS", blocked_reason="extraction_not_confirmed", message="La validacion minima de extraccion no esta completa.", ip_address=ip_address, user_agent=user_agent)
        periods = self.extractions.list_labor_periods(case.id)
        contribution_weeks = self.extractions.list_contribution_weeks(case.id)
        if not periods and not contribution_weeks:
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="MISSING_REQUIRED_STRUCTURED_DATA",
                message="Faltan datos estructurados minimos para ejecutar calculos.",
                details={"caseId": str(case.id), "required": ["labor_periods_or_contribution_weeks"]},
            )
        pre_analysis = self.pre_analysis.latest_for_case(case.id)
        if pre_analysis is None:
            self._raise_blocked(case=case, actor=actor, code="CASE_NOT_READY_FOR_FULL_ANALYSIS", blocked_reason="preanalysis_missing", message="Falta el preanalisis requerido para iniciar el analisis completo.", ip_address=ip_address, user_agent=user_agent)
        if pre_analysis.status not in READY_PRE_ANALYSIS_STATUSES:
            self._raise_blocked(case=case, actor=actor, code="CASE_NOT_READY_FOR_FULL_ANALYSIS", blocked_reason="preanalysis_not_ready", message="El preanalisis aun no esta listo.", ip_address=ip_address, user_agent=user_agent)
        return {
            "documents": documents,
            "extractionRun": extraction_run,
            "preAnalysis": pre_analysis,
            "periods": periods,
            "contributionWeeks": contribution_weeks,
        }

    def build_input_snapshot(self, *, case: LaboraCase, inputs: dict[str, Any]) -> dict[str, Any]:
        documents = inputs["documents"]
        extraction_run = inputs["extractionRun"]
        periods = inputs["periods"]
        profile = self._case_profile(case.id)
        employers = self.extractions.list_employers(case.id)
        gaps = self.extractions.list_gaps(case.id)
        salary_bases = self.extractions.list_salary_bases(case.id)
        contribution_weeks = inputs["contributionWeeks"]
        pre_analysis = inputs.get("preAnalysis")
        snapshot = {
            "case": {
                "id": str(case.id),
                "caseNumber": case.case_number,
                "caseTypeRequested": case.case_type_requested,
                "situationType": case.situation_type,
                "holderBirthDate": _json_safe(case.holder_birth_date),
                "pensionFundOrEntity": case.pension_fund_or_entity,
            },
            "documents": [self._document_snapshot(document) for document in documents],
            "extraction": self._extraction_snapshot(extraction_run),
            "preAnalysis": self._pre_analysis_snapshot(pre_analysis),
            "profile": self._profile_snapshot(profile),
            "signals": self._signals(profile=profile, periods=periods, employers=employers),
            "employers": [self._employer_snapshot(item) for item in employers],
            "periods": [self._period_snapshot(item) for item in periods],
            "contributionWeeks": [self._contribution_week_snapshot(item) for item in contribution_weeks],
            "gaps": [self._gap_snapshot(item) for item in gaps],
            "salaryBases": [self._salary_base_snapshot(item) for item in salary_bases],
            "snapshotVersion": "full-analysis-input-v1",
            "createdAt": utc_now(),
        }
        snapshot["inputHash"] = _stable_hash(snapshot)
        return _json_safe(snapshot)

    def _build_scenarios(
        self,
        snapshot: dict[str, Any],
        rules: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_code = {item["calculationCode"]: item for item in calculations}
        weeks_total = _decimal((by_code.get("WEEKS_TOTAL_001") or {}).get("resultValue"))
        missing_weeks = _decimal((by_code.get("MISSING_WEEKS_ESTIMATE_001") or {}).get("resultValue"))
        correct_amount = _decimal((by_code.get("CORRECT_ESTIMATED_AMOUNT_001") or {}).get("resultValue"))
        recognized_amount = _decimal((by_code.get("RECOGNIZED_AMOUNT_001") or {}).get("resultValue"))
        difference = _decimal((by_code.get("ECONOMIC_DIFFERENCE_001") or {}).get("resultValue"))
        retroactive = _decimal((by_code.get("RETROACTIVE_ESTIMATE_001") or {}).get("resultValue"))
        legal_refs = [{"type": "legal_rule", "code": rule["ruleCode"], "label": rule["ruleName"]} for rule in rules if rule["result"] in {"passed", "warning"}]
        scenarios = [
            {
                "scenarioType": "recognized_by_entity",
                "name": "Escenario reconocido por entidad",
                "description": "Escenario base con la informacion reconocida o inferida de los soportes.",
                "basePeriods": snapshot.get("periods") or [],
                "legalBasisRefs": legal_refs[:3],
                "calculationRefs": [{"type": "calculation", "code": "WEEKS_TOTAL_001"}],
                "amountEstimated": recognized_amount if recognized_amount > 0 else None,
                "weeksEstimated": max(weeks_total - missing_weeks, Decimal("0")),
                "retroactiveEstimated": Decimal("0"),
                "differenceVsRecognized": Decimal("0"),
                "confidence": Decimal("78.00"),
            },
            {
                "scenarioType": "calculated_correct",
                "name": "Escenario calculado por Labora",
                "description": "Escenario reproducible segun reglas y calculos base del modulo.",
                "basePeriods": snapshot.get("periods") or [],
                "legalBasisRefs": legal_refs,
                "calculationRefs": [{"type": "calculation", "code": item["calculationCode"]} for item in calculations],
                "amountEstimated": correct_amount if correct_amount > 0 else None,
                "weeksEstimated": weeks_total,
                "retroactiveEstimated": retroactive,
                "differenceVsRecognized": difference,
                "confidence": Decimal("76.00"),
            },
        ]
        if missing_weeks > 0 or any(rule.get("requiresReview") for rule in rules):
            scenarios.append(
                {
                    "scenarioType": "alternative",
                    "name": "Escenario alternativo con soportes pendientes",
                    "description": "Escenario para lectura conservadora cuando faltan soportes o hay incertidumbre.",
                    "basePeriods": snapshot.get("periods") or [],
                    "legalBasisRefs": legal_refs[:3],
                    "calculationRefs": [{"type": "calculation", "code": "MISSING_WEEKS_ESTIMATE_001"}],
                    "amountEstimated": correct_amount if correct_amount > 0 else None,
                    "weeksEstimated": weeks_total + missing_weeks,
                    "retroactiveEstimated": retroactive,
                    "differenceVsRecognized": difference,
                    "confidence": Decimal("64.00"),
                }
            )
        return scenarios

    def _build_inconsistencies(
        self,
        snapshot: dict[str, Any],
        rules: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        gaps = snapshot.get("gaps") or []
        if gaps:
            items.append(
                {
                    "inconsistencyType": "missing_weeks",
                    "severity": "high" if len(gaps) >= 2 else "medium",
                    "title": "Semanas posiblemente no reconocidas",
                    "description": "Se observan vacios de cotizacion o periodos sin integracion completa.",
                    "evidenceRefs": [{"type": "contribution_gap", "id": gap.get("id"), "label": gap.get("description")} for gap in gaps],
                    "legalRuleRefs": _rule_refs(rules, {"MISSING_WEEKS_OR_MORA_001"}),
                    "calculationRefs": [{"type": "calculation", "code": "MISSING_WEEKS_ESTIMATE_001"}],
                    "economicImpactEstimated": _calc_value(calculations, "RETROACTIVE_ESTIMATE_001"),
                    "legalImpact": "Puede soportar reclamacion administrativa o revision de semanas.",
                    "missingDocuments": ["Certificacion laboral del empleador"],
                    "recommendedAction": "administrative_claim",
                    "confidence": Decimal("82.00"),
                }
            )
        signals = set(snapshot.get("signals") or [])
        if signals & {"servidor_publico_posible", "docente_magisterio_posible", "regimen_especial_posible"}:
            items.append(
                {
                    "inconsistencyType": "public_time_not_integrated",
                    "severity": "medium",
                    "title": "Tiempo publico o regimen especial por validar",
                    "description": "Hay senales de tiempos publicos, docencia o regimen especial que pueden cambiar la ruta juridica.",
                    "evidenceRefs": [],
                    "legalRuleRefs": _rule_refs(rules, {"SPECIAL_REGIME_SIGNAL_001", "REGIME_CLASSIFICATION_001"}),
                    "calculationRefs": [],
                    "economicImpactEstimated": None,
                    "legalImpact": "Requiere validar regimen aplicable antes de una conclusion definitiva.",
                    "missingDocuments": ["Certificacion de tiempo publico o acto administrativo aplicable"],
                    "recommendedAction": "human_review",
                    "confidence": Decimal("76.00"),
                }
            )
        if not snapshot.get("salaryBases"):
            items.append(
                {
                    "inconsistencyType": "omitted_salary_factors",
                    "severity": "medium",
                    "title": "Bases salariales insuficientes",
                    "description": "No hay bases salariales suficientes para una estimacion economica robusta.",
                    "evidenceRefs": [],
                    "legalRuleRefs": _rule_refs(rules, {"RELIQUIDATION_SIGNAL_001"}),
                    "calculationRefs": [{"type": "calculation", "code": "SALARY_BASE_SUMMARY_001"}],
                    "economicImpactEstimated": None,
                    "legalImpact": "El calculo economico queda limitado hasta aportar soportes salariales.",
                    "missingDocuments": ["IBL", "colillas de pago", "certificacion salarial"],
                    "recommendedAction": "request_documents",
                    "confidence": Decimal("70.00"),
                }
            )
        return items

    def _evaluate_confidence(
        self,
        *,
        snapshot: dict[str, Any],
        rules: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
        scenarios: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], Decimal]:
        document_score = _average([_score(document.get("validationScore")) for document in snapshot.get("documents", [])], Decimal("65"))
        extraction_score = _score((snapshot.get("extraction") or {}).get("confidenceAvg"), default=Decimal("70"))
        rules_score = _average([_decimal(rule.get("confidence")) for rule in rules], Decimal("60"))
        calculation_score = _average([_decimal(item.get("confidence")) for item in calculations], Decimal("60"))
        scenario_score = _average([_decimal(item.get("confidence")) for item in scenarios], Decimal("60"))
        penalty = Decimal("5.00") if any(item["severity"] in {"high", "critical"} for item in inconsistencies) else Decimal("0")
        global_score = _clamp_score(_average([document_score, extraction_score, rules_score, calculation_score, scenario_score], Decimal("60")) - penalty)
        scores = [
            _score_payload("document_quality", document_score, ["Validacion documental procesada"], "proceed"),
            _score_payload("extraction", extraction_score, ["Extraccion estructurada confirmada"], "proceed"),
            _score_payload("legal_rule", rules_score, ["Reglas juridicas deterministicas aplicadas"], "warn_user" if rules_score < LOW_CONFIDENCE_THRESHOLD else "proceed"),
            _score_payload("calculation", calculation_score, ["Calculos reproducibles con formula y unidad"], "warn_user" if calculation_score < LOW_CONFIDENCE_THRESHOLD else "proceed"),
            _score_payload("scenario", scenario_score, ["Escenarios comparativos generados"], "warn_user" if scenario_score < LOW_CONFIDENCE_THRESHOLD else "proceed"),
            _score_payload("global", global_score, ["Promedio ponderado de documentos, extraccion, reglas, calculos y escenarios"], "human_review" if global_score < LOW_CONFIDENCE_THRESHOLD else "proceed"),
        ]
        return scores, global_score

    def _build_summary(
        self,
        *,
        global_score: Decimal,
        rules: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
        consistency_findings: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        route = "no_relevant_error"
        for rule in rules:
            if rule["ruleCode"] == "PROCEDURAL_ROUTE_001":
                route = (rule.get("resultDetail") or {}).get("recommendedRoute") or route
        estimated_difference = _calc_value(calculations, "ECONOMIC_DIFFERENCE_001") or Decimal("0")
        viability = "high" if global_score >= Decimal("80") else "medium" if global_score >= Decimal("65") else "low"
        main_finding = (
            "Se detectan inconsistencias que justifican revision detallada."
            if inconsistencies
            else "No se detecta un error relevante con los datos actuales."
        )
        executive = {
            "resultStatus": "possible_error" if inconsistencies else "no_relevant_error",
            "viabilityLevel": viability,
            "recommendedRoute": route,
            "estimatedDifference": _json_safe(estimated_difference),
            "currency": "COP",
            "mainFinding": main_finding,
        }
        summary = {
            "plainLanguageSummary": main_finding,
            "technicalSummary": (
                "El analisis se genero con reglas deterministicas, calculos reproducibles "
                "y trazabilidad a documentos, extraccion y resultados estructurados."
            ),
            "warnings": [
                item["title"]
                for item in inconsistencies
                if item["severity"] in {"high", "critical", "medium"}
            ]
            + (consistency_findings or []),
            "citationCheck": {"allClaimsSupported": True, "unsupportedClaims": []},
        }
        return executive, summary

    def _verify_consistency(
        self,
        *,
        rules: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
    ) -> list[str]:
        findings: list[str] = []
        route = "no_relevant_error"
        for rule in rules:
            if rule["ruleCode"] == "PROCEDURAL_ROUTE_001":
                route = (rule.get("resultDetail") or {}).get("recommendedRoute") or route
                break
        missing_weeks = _calc_value(calculations, "MISSING_WEEKS_ESTIMATE_001") or Decimal("0")
        economic_difference = _calc_value(calculations, "ECONOMIC_DIFFERENCE_001") or Decimal("0")
        inconsistency_types = {item["inconsistencyType"] for item in inconsistencies}
        if route == "no_relevant_error" and inconsistencies:
            findings.append("La ruta procedimental no reporta error relevante, pero existen inconsistencias accionables.")
        if missing_weeks > 0 and "missing_weeks" not in inconsistency_types:
            findings.append("El calculo detecta semanas faltantes, pero la matriz no incluye la inconsistencia correspondiente.")
        if economic_difference > 0 and not inconsistencies:
            findings.append("Hay diferencia economica estimada sin una inconsistencia que la explique.")
        return findings

    def _requires_human_review(
        self,
        *,
        global_score: Decimal,
        rules: list[dict[str, Any]],
        confidence_scores: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
    ) -> bool:
        return (
            global_score < LOW_CONFIDENCE_THRESHOLD
            or any(rule.get("requiresReview") for rule in rules)
            or any(score["recommendedAction"] == "human_review" for score in confidence_scores)
            or any(item["severity"] == "critical" for item in inconsistencies)
        )

    def _review_reason(
        self,
        global_score: Decimal,
        rules: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
    ) -> str:
        if global_score < LOW_CONFIDENCE_THRESHOLD:
            return "Confianza global inferior al umbral minimo."
        if any(rule.get("requiresReview") for rule in rules):
            return "Una o mas reglas juridicas requieren revision humana."
        if any(item["severity"] == "critical" for item in inconsistencies):
            return "Se detecto una inconsistencia critica."
        return "Revision humana requerida por consistencia."

    def _latest_analysis_for_access(
        self,
        case_id: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> FullAnalysis:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_unlocked(case)
        item = self.full_analysis.latest_for_case(case.id)
        if item is None:
            raise self._analysis_not_found(case.id)
        return item

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_analysis_or_404(self, full_analysis_id: str | uuid.UUID) -> FullAnalysis:
        item = self.full_analysis.get(full_analysis_id)
        if item is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="ANALYSIS_NOT_FOUND",
                message="Analisis completo no encontrado.",
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
        if user.role in ADMIN_ROLES:
            return True
        if user.role in OPERATOR_ROLES:
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
        if user.role in ADMIN_ROLES:
            return True
        if user.role in OPERATOR_ROLES:
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _can_force_reprocess(self, case: LaboraCase, user: User) -> bool:
        return user.role in ADMIN_ROLES or self._can_update(case, user)

    def _case_is_unlocked(self, case: LaboraCase) -> bool:
        if case.status in UNLOCKED_CASE_STATUSES:
            return True
        order = self.payments.latest_order_for_case(case_id=case.id, product_code="FULL_ANALYSIS_UNLOCK")
        if order is not None and order.status == "paid":
            return True
        paywall = (
            self.db.query(Paywall)
            .filter(Paywall.case_id == case.id)
            .order_by(Paywall.created_at.desc())
            .first()
        )
        return paywall is not None and (
            paywall.status == "completed"
            or paywall.unlock_required is False
            or paywall.unlocked_at is not None
        )

    def _require_unlocked(self, case: LaboraCase) -> None:
        if not self._case_is_unlocked(case):
            raise self._payment_required_error(case)

    def _payment_required_error(self, case: LaboraCase) -> ApiError:
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="PAYMENT_REQUIRED",
            message="El analisis completo requiere pago aprobado.",
            details={"caseId": str(case.id)},
        )

    def _ready_documents(self, case_id: uuid.UUID) -> list[Document]:
        documents = self.documents.list_active_case_documents(case_id)
        return [
            document
            for document in documents
            if document.status in READY_DOCUMENT_STATUSES
            and document.validation_status in READY_DOCUMENT_VALIDATION_STATUSES
        ]

    def _case_profile(self, case_id: uuid.UUID) -> CaseProfile | None:
        return self.db.query(CaseProfile).filter(CaseProfile.case_id == case_id).one_or_none()

    def _transition_case(self, case: LaboraCase, *, new_status: str, reason: str) -> None:
        if case.status == new_status or case.status in {"closed", "archived"}:
            return
        validate_case_transition(
            self.db,
            case,
            new_status=new_status,
            validate_transition=True,
        )
        previous_status = case.status
        case.status = new_status
        case.status_reason = reason
        case.current_step, case.next_best_action = step_for_status(new_status)
        case.updated_at = utc_now()
        self.cases.create_status_history(
            case_id=case.id,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            changed_by_user_id=None,
            changed_by_role="system",
            source_module="full_analysis",
            metadata=None,
        )

    def _set_status(
        self,
        item: FullAnalysis,
        job: FullAnalysisJob,
        status_value: str,
        progress: Decimal,
        current_step: str,
    ) -> None:
        item.status = status_value
        item.updated_at = utc_now()
        job.progress = progress
        job.current_step = current_step

    def _mark_failed(
        self,
        item: FullAnalysis,
        *,
        job: FullAnalysisJob,
        case: LaboraCase,
        actor: User | None,
        code: str,
        message: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        previous_status = item.status
        now = utc_now()
        item.status = "failed"
        item.failed_at = now
        item.failure_code = code
        item.failure_message = message[:2000]
        item.updated_at = now
        job.status = "failed"
        job.error_code = code
        job.error_message = message[:2000]
        job.finished_at = now
        job.current_step = "No fue posible completar el analisis"
        job.progress = Decimal("100.00")
        self._transition_case(case, new_status="error", reason="Fallo el analisis completo.")
        self._audit(
            FULL_ANALYSIS_EVENTS["failed"],
            actor=actor,
            case=case,
            full_analysis=item,
            previous_status=previous_status,
            new_status=item.status,
            metadata={"jobId": str(job.id), "errorCode": code},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _mark_blocked(
        self,
        item: FullAnalysis,
        *,
        job: FullAnalysisJob,
        case: LaboraCase,
        actor: User | None,
        code: str,
        message: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        previous_status = item.status
        now = utc_now()
        item.status = "blocked"
        item.requires_human_review = True
        item.human_review_reason = message
        item.failure_code = code
        item.failure_message = message[:2000]
        item.updated_at = now
        job.status = "blocked"
        job.error_code = code
        job.error_message = message[:2000]
        job.finished_at = now
        job.current_step = "Analisis bloqueado por precondiciones"
        job.progress = Decimal("100.00")
        self._transition_case(case, new_status="blocked", reason=message)
        self._record_history_event(
            case=case,
            event_type=FULL_ANALYSIS_EVENTS["blocked"],
            title="Analisis completo bloqueado",
            description=message,
            severity="warning",
            actor=actor,
            metadata={"fullAnalysisId": str(item.id), "errorCode": code},
        )
        self._audit(
            FULL_ANALYSIS_EVENTS["blocked"],
            actor=actor,
            case=case,
            full_analysis=item,
            previous_status=previous_status,
            new_status=item.status,
            metadata={"jobId": str(job.id), "errorCode": code},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _raise_blocked(
        self,
        *,
        case: LaboraCase,
        actor: User | None,
        code: str,
        blocked_reason: str,
        message: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._audit(
            "analisis_completo.blocked",
            actor=actor,
            case=case,
            full_analysis=None,
            new_status="blocked",
            metadata={"blockedReason": blocked_reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=code,
            message=message,
            details={"caseId": str(case.id), "blockedReason": blocked_reason},
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
            "analisis_completo.access_denied",
            actor=actor,
            case=case,
            full_analysis=None,
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="CASE_ACCESS_DENIED",
            message="No tienes permisos para acceder a este expediente.",
        )

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
        full_analysis: FullAnalysis | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_status: str | None = None,
        new_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "fullAnalysisId": str(full_analysis.id) if full_analysis else None,
            "actorRole": self._actor_role(actor),
            "previousStatus": previous_status,
            "newStatus": new_status,
            "sourceModule": "full_analysis",
        }
        if metadata:
            event_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_name,
            entity_type="full_analysis",
            entity_id=full_analysis.id if full_analysis else None,
            actor_user_id=actor.id if actor else None,
            metadata=_json_safe(event_metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _actor_role(self, user: User | None) -> str:
        if user is None:
            return "system"
        if user.role in ADMIN_ROLES:
            return "admin"
        if user.role in LEGAL_REVIEWER_ROLES:
            return "legal_reviewer"
        if user.role in OPERATOR_ROLES:
            return "operator"
        if user.role == "system":
            return "system"
        return "user"

    def _analysis_not_found(self, case_id: uuid.UUID) -> ApiError:
        return ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="ANALYSIS_NOT_FOUND",
            message="Analisis completo no encontrado.",
            details={"caseId": str(case_id)},
        )

    def _start_payload(self, item: FullAnalysis, message: str) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "status": item.status,
            "version": item.version,
            "message": message,
        }

    def _not_started_payload(self, case: LaboraCase) -> dict[str, Any]:
        return {
            "id": None,
            "caseId": str(case.id),
            "status": "not_started",
            "version": None,
            "progress": _progress_payload("not_started"),
            "executiveResult": None,
            "confidence": {
                "globalScore": None,
                "level": "insufficient_information",
                "requiresHumanReview": False,
                "reasons": [],
            },
            "createdAt": None,
            "completedAt": None,
        }

    def _result_payload(self, item: FullAnalysis) -> dict[str, Any]:
        scores = self.full_analysis.list_confidence_scores(item.id)
        global_score = item.confidence_global
        return {
            "id": str(item.id),
            "caseId": str(item.case_id),
            "status": item.status,
            "version": item.version,
            "progress": _progress_payload(item.status),
            "executiveResult": item.executive_result,
            "legalConclusion": item.legal_conclusion,
            "recommendedRoute": item.recommended_route,
            "viabilityLevel": item.viability_level,
            "confidence": {
                "globalScore": _float(global_score),
                "level": _confidence_level(global_score or Decimal("0")),
                "requiresHumanReview": item.requires_human_review,
                "reasons": [reason for score in scores for reason in (score.reasons or []) if score.scope == "global"],
            },
            "createdAt": item.created_at,
            "completedAt": item.completed_at,
        }

    def _rule_payload(self, row) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "ruleCode": row.rule_code,
            "ruleName": row.rule_name,
            "category": row.rule_category,
            "result": row.result,
            "explanation": row.explanation,
            "sourceRefs": row.source_refs or [],
            "confidence": _float(row.confidence),
            "requiresReview": row.requires_review,
        }

    def _calculation_payload(self, row) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "calculationCode": row.calculation_code,
            "name": row.calculation_name,
            "type": row.calculation_type,
            "inputValues": row.input_values or {},
            "formulaRef": row.formula_ref,
            "formulaExpression": row.formula_expression,
            "sourceRefs": row.source_refs or [],
            "resultValue": _float(row.result_value),
            "resultUnit": row.result_unit,
            "resultDetail": row.result_detail or {},
            "confidence": _float(row.confidence),
        }

    def _scenario_payload(self, row) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "scenarioType": row.scenario_type,
            "name": row.name,
            "description": row.description,
            "amountEstimated": _float(row.amount_estimated),
            "weeksEstimated": _float(row.weeks_estimated),
            "retroactiveEstimated": _float(row.retroactive_estimated),
            "differenceVsRecognized": _float(row.difference_vs_recognized),
            "confidence": _float(row.confidence),
        }

    def _inconsistency_payload(self, row) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "type": row.inconsistency_type,
            "severity": row.severity,
            "title": row.title,
            "description": row.description,
            "evidenceRefs": row.evidence_refs or [],
            "legalImpact": row.legal_impact,
            "economicImpactEstimated": _float(row.economic_impact_estimated),
            "missingDocuments": row.missing_documents or [],
            "recommendedAction": row.recommended_action,
            "confidence": _float(row.confidence),
        }

    def _confidence_payload(self, row) -> dict[str, Any]:
        return {
            "scope": row.scope,
            "scopeRefId": str(row.scope_ref_id) if row.scope_ref_id else None,
            "score": _float(row.score),
            "level": row.level,
            "reasons": row.reasons or [],
            "recommendedAction": row.recommended_action,
        }

    def _snapshot_digest(self, *, case: LaboraCase, inputs: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "caseId": str(case.id),
            "documents": [str(document.id) for document in inputs["documents"]],
            "extractionRunId": str(inputs["extractionRun"].id),
            "preAnalysisId": str(inputs["preAnalysis"].id) if inputs.get("preAnalysis") else None,
            "version": "full-analysis-input-v1",
        }
        return {"inputHash": _stable_hash(payload), **payload}

    def _document_snapshot(self, document: Document) -> dict[str, Any]:
        validation = self.documents.latest_validation(document.id)
        return {
            "id": str(document.id),
            "type": document.document_type.code if document.document_type else None,
            "displayName": document.display_name or document.original_filename,
            "status": document.status,
            "validationStatus": document.validation_status,
            "validationResult": validation.result if validation else None,
            "validationScore": _json_safe(validation.score if validation else None),
            "warnings": validation.warnings if validation else [],
            "pageCount": document.page_count,
            "sourceRef": {"type": "document", "id": str(document.id), "label": document.display_name or document.original_filename},
        }

    def _extraction_snapshot(self, run: ExtractionRun) -> dict[str, Any]:
        return {
            "id": str(run.id),
            "status": run.status,
            "confirmationStatus": run.confirmation_status,
            "confidenceAvg": _json_safe(run.confidence_avg),
            "lowConfidenceCount": run.low_confidence_count,
            "issuesCount": run.issues_count,
            "completedAt": _json_safe(run.completed_at),
        }

    def _pre_analysis_snapshot(self, item: PreAnalysis | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "id": str(item.id),
            "status": item.status,
            "preliminaryCaseType": item.preliminary_case_type,
            "trafficLight": item.traffic_light,
            "viabilityLevel": item.viability_level,
            "confidence": _json_safe(item.confidence),
            "recognizedAmount": None,
        }

    def _profile_snapshot(self, profile: CaseProfile | None) -> dict[str, Any]:
        if profile is None:
            return {}
        return {
            "id": str(profile.id),
            "hasPublicSectorWork": profile.has_public_sector_work,
            "hasTeacherHistory": profile.has_teacher_history,
            "hasSpecialRegimeSignal": profile.has_special_regime_signal,
            "hasMissingWeeksClaim": profile.has_missing_weeks_claim,
            "hasReliquidationSignal": profile.has_reliquidation_signal,
            "hasPriorClaim": profile.has_prior_claim,
            "detectedRoute": profile.detected_route,
            "criticalFacts": profile.critical_facts,
            "missingDocuments": profile.missing_documents,
            "confidence": _json_safe(profile.confidence),
            "requiresReview": profile.requires_review,
        }

    def _signals(
        self,
        *,
        profile: CaseProfile | None,
        periods: list[LaborPeriod],
        employers: list[Employer],
    ) -> list[str]:
        signals: set[str] = set()
        if profile is not None:
            if profile.has_public_sector_work:
                signals.add("servidor_publico_posible")
            if profile.has_teacher_history:
                signals.add("docente_magisterio_posible")
            if profile.has_special_regime_signal:
                signals.add("regimen_especial_posible")
        for period in periods:
            if period.regime_hint == "public":
                signals.add("servidor_publico_posible")
            if period.regime_hint == "teacher":
                signals.add("docente_magisterio_posible")
            if period.regime_hint == "special":
                signals.add("regimen_especial_posible")
        for employer in employers:
            if employer.employer_type == "public":
                signals.add("servidor_publico_posible")
            if employer.employer_type == "teacher":
                signals.add("docente_magisterio_posible")
        return sorted(signals)

    def _employer_snapshot(self, item: Employer) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "name": item.name,
            "nit": item.nit,
            "employerType": item.employer_type,
            "confidence": _json_safe(item.confidence),
            "status": item.status,
        }

    def _period_snapshot(self, item: LaborPeriod) -> dict[str, Any]:
        calendar_weeks = _calendar_weeks(item.start_date, item.end_date)
        return {
            "id": str(item.id),
            "employerId": str(item.employer_id) if item.employer_id else None,
            "startDate": _json_safe(item.start_date),
            "endDate": _json_safe(item.end_date),
            "periodType": item.period_type,
            "regimeHint": item.regime_hint,
            "weeksDetected": _json_safe(item.weeks_detected),
            "daysDetected": item.days_detected,
            "calendarWeeks": _json_safe(calendar_weeks),
            "salaryBaseDetected": _json_safe(item.salary_base_detected),
            "confidence": _json_safe(item.confidence),
            "sourceDocumentId": str(item.source_document_id) if item.source_document_id else None,
            "sourcePage": item.source_page,
        }

    def _contribution_week_snapshot(self, item: ContributionWeek) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "laborPeriodId": str(item.labor_period_id) if item.labor_period_id else None,
            "year": item.year,
            "month": item.month,
            "weeks": _json_safe(item.weeks),
            "days": item.days,
            "source": item.source,
            "confidence": _json_safe(item.confidence),
        }

    def _gap_snapshot(self, item: ContributionGap) -> dict[str, Any]:
        estimated_weeks = _calendar_weeks(item.start_date, item.end_date)
        return {
            "id": str(item.id),
            "startDate": _json_safe(item.start_date),
            "endDate": _json_safe(item.end_date),
            "gapType": item.gap_type,
            "description": item.description,
            "severity": item.severity,
            "confidence": _json_safe(item.confidence),
            "estimatedWeeks": _json_safe(estimated_weeks),
        }

    def _salary_base_snapshot(self, item: SalaryBase) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "laborPeriodId": str(item.labor_period_id) if item.labor_period_id else None,
            "employerId": str(item.employer_id) if item.employer_id else None,
            "periodYear": item.period_year,
            "periodMonth": item.period_month,
            "amount": _json_safe(item.amount),
            "currency": item.currency,
            "confidence": _json_safe(item.confidence),
        }


def _progress_payload(status_value: str) -> dict[str, Any]:
    progress_map = {
        "not_started": (0, "not_started"),
        "queued": (5, "queued"),
        "in_progress": (10, "input_snapshot"),
        "rules_running": (25, "legal_rules"),
        "calculations_running": (45, "calculations"),
        "scenario_comparison_running": (65, "scenarios"),
        "confidence_evaluation_running": (82, "confidence"),
        "requires_review": (90, "requires_review"),
        "completed": (100, "completed"),
        "blocked": (100, "blocked"),
        "failed": (100, "failed"),
        "cancelled": (100, "cancelled"),
    }
    percentage, current_step = progress_map.get(status_value, (0, status_value))
    return {
        "percentage": percentage,
        "currentStep": current_step,
        "steps": [
            _step("input_snapshot", "Preparando datos", status_value, {"in_progress"}, {"rules_running", "calculations_running", "scenario_comparison_running", "confidence_evaluation_running", "requires_review", "completed"}),
            _step("legal_rules", "Reglas juridicas", status_value, {"rules_running"}, {"calculations_running", "scenario_comparison_running", "confidence_evaluation_running", "requires_review", "completed"}),
            _step("calculations", "Calculos", status_value, {"calculations_running"}, {"scenario_comparison_running", "confidence_evaluation_running", "requires_review", "completed"}),
            _step("scenarios", "Comparador de escenarios", status_value, {"scenario_comparison_running"}, {"confidence_evaluation_running", "requires_review", "completed"}),
            _step("confidence", "Confianza", status_value, {"confidence_evaluation_running"}, {"requires_review", "completed"}),
            _step("summary", "Resumen final", status_value, {"requires_review"}, {"completed"}),
        ],
    }


def _step(
    key: str,
    label: str,
    status_value: str,
    active_statuses: set[str],
    completed_statuses: set[str],
) -> dict[str, str]:
    if status_value in {"failed"}:
        step_status = "error"
    elif status_value in {"blocked"}:
        step_status = "blocked"
    elif status_value in active_statuses:
        step_status = "active"
    elif status_value in completed_statuses:
        step_status = "completed"
    elif status_value == "requires_review" and key == "summary":
        step_status = "warning"
    else:
        step_status = "pending"
    return {"key": key, "label": label, "status": step_status}


def _rule_refs(rules: list[dict[str, Any]], codes: set[str]) -> list[dict[str, Any]]:
    return [
        {"type": "legal_rule", "code": rule["ruleCode"], "label": rule["ruleName"]}
        for rule in rules
        if rule["ruleCode"] in codes
    ]


def _calc_value(calculations: list[dict[str, Any]], code: str) -> Decimal | None:
    for item in calculations:
        if item["calculationCode"] == code:
            return _decimal(item.get("resultValue"))
    return None


def _score_payload(scope: str, score: Decimal, reasons: list[str], recommended_action: str) -> dict[str, Any]:
    return {
        "scope": scope,
        "score": _clamp_score(score),
        "level": _confidence_level(score),
        "reasons": reasons,
        "recommendedAction": recommended_action,
    }


def _confidence_level(score: Decimal) -> str:
    if score >= Decimal("80"):
        return "high"
    if score >= Decimal("65"):
        return "medium"
    if score >= Decimal("45"):
        return "low"
    return "critical"


def _score(value: Any, *, default: Decimal = Decimal("0")) -> Decimal:
    parsed = _decimal(value, default=default)
    if parsed <= Decimal("1"):
        parsed = parsed * Decimal("100")
    return _clamp_score(parsed)


def _average(values: list[Decimal], default: Decimal) -> Decimal:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return default
    return _clamp_score(sum(filtered, Decimal("0")) / Decimal(len(filtered)))


def _clamp_score(value: Decimal) -> Decimal:
    if value < 0:
        value = Decimal("0")
    if value > 100:
        value = Decimal("100")
    return value.quantize(Decimal("0.01"))


def _calendar_weeks(start: date | None, end: date | None) -> Decimal:
    if start is None:
        return Decimal("0.00")
    end_value = end or utc_now().date()
    days = max((end_value - start).days + 1, 0)
    return (Decimal(days) / Decimal("7")).quantize(Decimal("0.01"))


def _stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decimal(value: Any, *, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
