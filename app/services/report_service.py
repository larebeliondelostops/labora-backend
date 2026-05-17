import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape as xml_escape

from fastapi import status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.full_analysis import (
    AnalysisInconsistency,
    CalculationResult,
    FullAnalysis,
    LegalRuleResult,
    Scenario,
)
from app.models.paywall import Paywall
from app.models.report import ExportFile, Report, ReportGenerationJob, ReportVersion
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.full_analysis_repository import FullAnalysisRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.report_repository import (
    ACTIVE_REPORT_STATUSES,
    REUSABLE_REPORT_STATUSES,
    ReportRepository,
)
from app.schemas.report import ReportCreateRequest, ReportExportRequest
from app.services.consent_service import ConsentComplianceService
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.services.report_ai_client import build_report_ai_client
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
OPERATOR_ROLES = {"operator", "legal_ops", "reviewer", "support_agent", "support"}
REVIEW_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES}
INTERNAL_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, *OPERATOR_ROLES, "system"}
LOW_CONFIDENCE_THRESHOLD = Decimal("70.00")
LOCKED_CASE_STATUSES = {"closed", "archived", "blocked"}
UNLOCKED_CASE_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
    "requires_review",
    "result_completed",
    "report_ready",
}
READY_ANALYSIS_STATUSES = {"completed", "requires_review"}
CALCULATION_REPORT_TYPES = {"calculation", "technical", "full"}
EXPORT_READY_REPORT_STATUSES = {"ready", "approved"}
DOWNLOAD_TOKEN_TTL_SECONDS = 600

REPORT_EVENTS = {
    "created": "informes.created",
    "updated": "informes.updated",
    "viewed": "informes.viewed",
    "submitted": "informes.submitted",
    "approved": "informes.approved",
    "rejected": "informes.rejected",
    "failed": "informes.failed",
    "export_requested": "informes.export_requested",
    "export_ready": "informes.export_ready",
    "export_failed": "informes.export_failed",
    "export_downloaded": "informes.export_downloaded",
    "version_created": "informes.version_created",
    "requires_review": "informes.requires_review",
    "access_denied": "informes.access_denied",
}

REPORT_TYPE_TITLES = {
    "executive": "Informe ejecutivo",
    "technical": "Informe tecnico-juridico",
    "calculation": "Informe de calculo",
    "inconsistency_matrix": "Matriz de inconsistencias",
    "full": "Informe completo",
}

DEFAULT_TEMPLATE_KEYS = {
    "executive": "labora_executive_report_v1",
    "technical": "labora_technical_report_v1",
    "calculation": "labora_calculation_report_v1",
    "inconsistency_matrix": "labora_inconsistency_matrix_v1",
    "full": "labora_full_report_v1",
}

DEFAULT_TEMPLATES = {
    key: {
        "template_key": key,
        "name": REPORT_TYPE_TITLES[report_type],
        "report_type": report_type,
        "version": "1.0.0",
        "status": "active",
        "template_markdown": "{{ sections }}",
        "schema_json": {"sectionKeys": "controlled"},
    }
    for report_type, key in DEFAULT_TEMPLATE_KEYS.items()
}

SECTION_ORDER = [
    "cover",
    "traceability_stamp",
    "executive_summary",
    "case_context",
    "relevant_facts",
    "labor_timeline",
    "applicable_regime",
    "legal_rules",
    "calculation_summary",
    "calculation_detail",
    "inconsistency_matrix",
    "conclusions",
    "recommended_route",
    "missing_documents",
    "evidence_index",
    "ai_disclaimer",
    "professional_review_warning",
    "appendix",
]

REPORT_TYPE_SECTIONS = {
    "executive": [
        "cover",
        "traceability_stamp",
        "executive_summary",
        "case_context",
        "calculation_summary",
        "missing_documents",
        "recommended_route",
        "ai_disclaimer",
        "professional_review_warning",
    ],
    "technical": [
        "cover",
        "traceability_stamp",
        "case_context",
        "relevant_facts",
        "legal_rules",
        "calculation_detail",
        "conclusions",
        "evidence_index",
        "ai_disclaimer",
        "professional_review_warning",
    ],
    "calculation": [
        "cover",
        "traceability_stamp",
        "calculation_summary",
        "calculation_detail",
        "conclusions",
        "ai_disclaimer",
    ],
    "inconsistency_matrix": [
        "cover",
        "traceability_stamp",
        "inconsistency_matrix",
        "missing_documents",
        "evidence_index",
        "ai_disclaimer",
    ],
    "full": SECTION_ORDER,
}


class ReportTemplateService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.reports = ReportRepository(db)

    def resolve(self, *, report_type: str, template_key: str | None):
        key = template_key or DEFAULT_TEMPLATE_KEYS[report_type]
        template = self.reports.active_template(template_key=key, report_type=report_type)
        if template is not None:
            return template
        if key not in DEFAULT_TEMPLATES:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_TEMPLATE_NOT_FOUND",
                message="La plantilla de informe solicitada no existe.",
                details={"templateKey": key, "reportType": report_type},
            )
        template = self.reports.create_template(**DEFAULT_TEMPLATES[key])
        self.db.flush()
        return template


class ReportVersionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.reports = ReportRepository(db)

    def create_current_version(
        self,
        *,
        report: Report,
        sections: list[dict[str, Any]],
        source_hash: str,
        change_summary: str,
        actor: User | None,
        actor_role: str,
    ) -> ReportVersion:
        snapshot_markdown = _sections_markdown(sections)
        snapshot_json = {
            "report": {
                "id": str(report.id),
                "caseId": str(report.case_id),
                "reportType": report.report_type,
                "title": report.title,
                "status": report.status,
                "language": report.language,
                "sourceAnalysisId": str(report.source_analysis_id) if report.source_analysis_id else None,
            },
            "sections": [
                {
                    "sectionKey": item["section_key"],
                    "title": item["title"],
                    "contentMarkdown": item["content_markdown"],
                    "contentJson": item.get("content_json"),
                    "orderIndex": item["order_index"],
                    "confidence": _json_safe(item.get("confidence")),
                    "sourceRefs": item.get("source_refs") or [],
                    "aiMetadata": item.get("ai_metadata"),
                }
                for item in sections
            ],
        }
        content_hash = _stable_hash({"markdown": snapshot_markdown, "json": snapshot_json})
        self.reports.mark_versions_superseded(report.id)
        version = self.reports.create_version(
            report_id=report.id,
            version_number=self.reports.next_version_number(report.id),
            status="current",
            snapshot_json=_json_safe(snapshot_json),
            snapshot_markdown=snapshot_markdown,
            source_hash=source_hash,
            content_hash=content_hash,
            change_summary=change_summary,
            created_by=actor.id if actor else None,
            created_by_role=actor_role,
            created_at=utc_now(),
        )
        report.current_version_id = version.id
        report.updated_at = utc_now()
        return version


class ReportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.full_analysis = FullAnalysisRepository(db)
        self.payments = PaymentRepository(db)
        self.reports = ReportRepository(db)
        self.audit_events = AuditEventRepository(db)
        self.templates = ReportTemplateService(db)
        self.versions = ReportVersionService(db)
        self.ai_client = build_report_ai_client()

    def create_or_reuse(
        self,
        case_id: str,
        payload: ReportCreateRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        template = self.templates.resolve(
            report_type=payload.report_type,
            template_key=payload.template_key,
        )

        existing_active = self.reports.latest_for_case_type(
            case_id=case.id,
            report_type=payload.report_type,
            statuses=ACTIVE_REPORT_STATUSES,
        )
        if existing_active is not None and not payload.force_regenerate:
            job = self.reports.latest_job(report_id=existing_active.id, job_type="generate_report")
            return self._queued_payload(existing_active, job), status.HTTP_202_ACCEPTED

        reusable = self.reports.latest_for_case_type(
            case_id=case.id,
            report_type=payload.report_type,
            statuses=REUSABLE_REPORT_STATUSES,
        )
        if reusable is not None and not payload.force_regenerate:
            self._audit(
                REPORT_EVENTS["viewed"],
                actor=user,
                case=case,
                report=reusable,
                metadata={"reused": True, "reportType": payload.report_type},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return self._ready_payload(reusable), status.HTTP_200_OK

        analysis = self._validated_sources(case=case, report_type=payload.report_type, actor=user)
        now = utc_now()
        report = self.reports.create_report(
            case_id=case.id,
            owner_user_id=case.owner_user_id,
            report_type=payload.report_type,
            title=self._title(case, payload.report_type),
            status="queued",
            language="es-CO",
            visibility="user",
            source_analysis_id=analysis.id,
            ai_confidence=analysis.confidence_global,
            requires_human_review=False,
            generated_by=self._actor_role(user),
            created_at=now,
            updated_at=now,
        )
        job = self.reports.create_job(
            case_id=case.id,
            report_id=report.id,
            job_type="generate_report",
            status="queued",
            attempts=0,
            provider="deterministic",
            model="labora-report-writer-v1",
            created_at=now,
            updated_at=now,
        )
        self._record_history_event(
            case=case,
            event_type=REPORT_EVENTS["created"],
            title="Informe iniciado",
            description="La generacion del informe fue puesta en cola.",
            severity="info",
            actor=user,
            metadata={"reportId": str(report.id), "reportType": payload.report_type},
        )
        self._audit(
            REPORT_EVENTS["created"],
            actor=user,
            case=case,
            report=report,
            new_status=report.status,
            metadata={
                "reportType": payload.report_type,
                "templateKey": template.template_key,
                "forceRegenerate": payload.force_regenerate,
                "includeSections": payload.include_sections,
                "outputMode": payload.output_mode,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            REPORT_EVENTS["submitted"],
            actor=user,
            case=case,
            report=report,
            new_status=report.status,
            metadata={"jobId": str(job.id), "templateKey": template.template_key},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        job.input_hash = _stable_hash(
            {
                "caseId": str(case.id),
                "reportType": payload.report_type,
                "analysisId": str(analysis.id),
                "includeSections": payload.include_sections,
            }
        )
        self.db.commit()
        return self._queued_payload(report, job), status.HTTP_202_ACCEPTED

    def run_generation(
        self,
        report_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
        include_sections: list[str] | None = None,
    ) -> dict[str, Any]:
        report = self._get_report_or_404(report_id)
        case = self._get_case_or_404(report.case_id)
        job = self.reports.latest_job(report_id=report.id, job_type="generate_report")
        if job is None:
            job = self.reports.create_job(
                case_id=case.id,
                report_id=report.id,
                job_type="generate_report",
                status="queued",
                attempts=0,
            )
        if report.status in {"ready", "approved", "requires_review"} and report.current_version_id:
            return self._ready_payload(report)
        try:
            analysis = self._validated_sources(case=case, report_type=report.report_type, actor=actor)
            previous_status = report.status
            now = utc_now()
            report.status = "generating"
            report.updated_at = now
            job.status = "generating"
            job.started_at = job.started_at or now
            job.attempts += 1
            job.updated_at = now
            self.db.commit()

            source_payload = self._source_payload(case=case, analysis=analysis)
            source_hash = _stable_hash(source_payload)
            sections = self._build_sections(
                case=case,
                report=report,
                analysis=analysis,
                source_payload=source_payload,
                include_sections=include_sections or [],
            )
            requires_review, review_reason = self._requires_review(analysis=analysis, sections=sections)
            report.status = "requires_review" if requires_review else "ready"
            report.requires_human_review = requires_review
            report.review_reason = review_reason
            report.ai_confidence = analysis.confidence_global
            report.source_analysis_id = analysis.id
            report.updated_at = utc_now()
            self.reports.replace_sections(report, sections)
            version = self.versions.create_current_version(
                report=report,
                sections=sections,
                source_hash=source_hash,
                change_summary="Generacion automatica del informe",
                actor=actor,
                actor_role=self._actor_role(actor),
            )
            job.status = "completed"
            job.provider = _first_ai_metadata(sections, "provider")
            job.model = _first_ai_metadata(sections, "model")
            job.prompt_hash = _first_ai_metadata(sections, "promptHash")
            job.input_hash = source_hash
            job.output_hash = version.content_hash
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            self._record_history_event(
                case=case,
                event_type=REPORT_EVENTS["version_created"],
                title="Informe generado",
                description="Se creo una version inmutable del informe.",
                severity="warning" if requires_review else "success",
                actor=actor,
                metadata={
                    "reportId": str(report.id),
                    "versionId": str(version.id),
                    "versionNumber": version.version_number,
                    "requiresHumanReview": requires_review,
                },
            )
            self._audit(
                REPORT_EVENTS["version_created"],
                actor=actor,
                case=case,
                report=report,
                previous_status=previous_status,
                new_status=report.status,
                metadata={"versionId": str(version.id), "versionNumber": version.version_number},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            if requires_review:
                self._audit(
                    REPORT_EVENTS["requires_review"],
                    actor=actor,
                    case=case,
                    report=report,
                    previous_status=previous_status,
                    new_status=report.status,
                    metadata={"reason": review_reason},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            else:
                self._audit(
                    REPORT_EVENTS["updated"],
                    actor=actor,
                    case=case,
                    report=report,
                    previous_status=previous_status,
                    new_status=report.status,
                    metadata={"sourceHash": source_hash},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            self.db.commit()
            return self._ready_payload(report)
        except ApiError:
            self.db.rollback()
            raise
        except Exception as exc:
            self.db.rollback()
            report = self._get_report_or_404(report_id)
            case = self._get_case_or_404(report.case_id)
            job = self.reports.latest_job(report_id=report.id, job_type="generate_report")
            previous_status = report.status
            report.status = "failed"
            report.review_reason = str(exc)[:2000]
            report.updated_at = utc_now()
            if job:
                job.status = "failed"
                job.error_code = "REPORT_GENERATION_FAILED"
                job.error_message = str(exc)[:2000]
                job.finished_at = utc_now()
                job.updated_at = utc_now()
            self._audit(
                REPORT_EVENTS["failed"],
                actor=actor,
                case=case,
                report=report,
                previous_status=previous_status,
                new_status=report.status,
                metadata={"error": str(exc)[:500]},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return self._ready_payload(report)

    def list_reports(
        self,
        case_id: str,
        *,
        report_type: str | None,
        report_status: str | None,
        page: int,
        limit: int,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        items, total = self.reports.list_for_case(
            case_id=case.id,
            report_type=report_type,
            status=report_status,
            page=page,
            limit=limit,
        )
        return {
            "items": [self._list_item(item) for item in items],
            "pagination": {"page": page, "limit": limit, "total": total},
        }

    def get_report(
        self,
        report_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        report = self._get_report_or_404(report_id)
        case = self._get_case_or_404(report.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        version = self.reports.current_version(report)
        self._audit(
            REPORT_EVENTS["viewed"],
            actor=user,
            case=case,
            report=report,
            metadata={"surface": "detail"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._detail_payload(report, version)

    def list_versions(
        self,
        report_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        report = self._get_report_or_404(report_id)
        case = self._get_case_or_404(report.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        return {"items": [self._version_item(item) for item in self.reports.list_versions(report.id)]}

    def approve(
        self,
        report_id: str,
        *,
        review_notes: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_review_role(user)
        report = self._get_report_or_404(report_id)
        case = self._get_case_or_404(report.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        previous_status = report.status
        report.status = "approved"
        report.requires_human_review = False
        report.review_reason = review_notes or report.review_reason
        report.approved_by = user.id
        report.approved_at = utc_now()
        report.updated_at = utc_now()
        self._audit(
            REPORT_EVENTS["approved"],
            actor=user,
            case=case,
            report=report,
            previous_status=previous_status,
            new_status=report.status,
            metadata={"reviewNotes": review_notes},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._ready_payload(report)

    def reject(
        self,
        report_id: str,
        *,
        reason: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_review_role(user)
        report = self._get_report_or_404(report_id)
        case = self._get_case_or_404(report.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        previous_status = report.status
        report.status = "rejected"
        report.requires_human_review = True
        report.review_reason = reason
        report.updated_at = utc_now()
        self._audit(
            REPORT_EVENTS["rejected"],
            actor=user,
            case=case,
            report=report,
            previous_status=previous_status,
            new_status=report.status,
            metadata={"reason": reason},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._ready_payload(report)

    def _validated_sources(
        self,
        *,
        case: LaboraCase,
        report_type: str,
        actor: User | None,
    ) -> FullAnalysis:
        if case.status in LOCKED_CASE_STATUSES or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_SOURCE_DATA_INCOMPLETE",
                message="El expediente tiene un bloqueo vigente para generar informes.",
                details={"caseId": str(case.id), "status": case.status},
            )
        permission = ConsentComplianceService(self.db).can_upload_documents(case.owner_user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_SOURCE_DATA_INCOMPLETE",
                message="El informe requiere consentimiento vigente.",
                details={"caseId": str(case.id), "missingConsentTypes": permission.missing_consent_types},
            )
        if not self._case_is_unlocked(case):
            raise ApiError(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                code="REPORT_PAYMENT_REQUIRED",
                message="El informe requiere pago aprobado o desbloqueo equivalente.",
                details={"caseId": str(case.id)},
            )
        analysis = self.full_analysis.latest_for_case(case.id)
        if analysis is None or analysis.status not in READY_ANALYSIS_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_ANALYSIS_NOT_READY",
                message="El analisis completo del expediente aun no esta disponible.",
                details={
                    "caseId": str(case.id),
                    "requiredStatus": "completed",
                    "currentStatus": analysis.status if analysis else "not_started",
                },
            )
        if report_type in CALCULATION_REPORT_TYPES:
            calculations = self._calculations(analysis.id)
            if not calculations:
                raise ApiError(
                    status_code=status.HTTP_409_CONFLICT,
                    code="REPORT_CALCULATION_NOT_READY",
                    message="El calculo requerido para este informe aun no esta disponible.",
                    details={"caseId": str(case.id), "analysisId": str(analysis.id)},
                )
        return analysis

    def _build_sections(
        self,
        *,
        case: LaboraCase,
        report: Report,
        analysis: FullAnalysis,
        source_payload: dict[str, Any],
        include_sections: list[str],
    ) -> list[dict[str, Any]]:
        section_keys = _selected_sections(report.report_type, include_sections)
        ai_executive = self.ai_client.generate_executive_summary(source_payload)
        ai_technical = self.ai_client.generate_technical_narrative(source_payload)
        refs = {
            "case": [{"type": "case", "id": str(case.id), "label": case.case_number}],
            "analysis": [{"type": "analysis_result", "id": str(analysis.id), "label": "Analisis completo"}],
            "rules": [
                {"type": "legal_rule", "id": item["id"], "label": item["ruleName"]}
                for item in source_payload["rules"]
            ],
            "calculations": [
                {"type": "calculation", "id": item["id"], "label": item["calculationName"]}
                for item in source_payload["calculations"]
            ],
            "inconsistencies": [
                {"type": "inconsistency", "id": item["id"], "label": item["title"]}
                for item in source_payload["inconsistencies"]
            ],
        }
        builders = {
            "cover": lambda: _section(
                "cover",
                "Portada",
                f"# {report.title}\n\nExpediente: **{case.case_number}**\n\nTitular: **{case.holder_first_name} {case.holder_last_name}**",
                refs["case"],
                confidence=100,
            ),
            "traceability_stamp": lambda: _section(
                "traceability_stamp",
                "Sello de trazabilidad",
                f"Fuente: analisis completo `{analysis.id}`.\n\nHash de fuente: `{_stable_hash(source_payload)}`.",
                refs["analysis"],
                confidence=100,
            ),
            "executive_summary": lambda: _section(
                "executive_summary",
                "Resumen ejecutivo",
                ai_executive.content_markdown,
                refs["analysis"] + refs["calculations"][:2] + refs["inconsistencies"][:3],
                confidence=ai_executive.confidence,
                ai_metadata=ai_executive.metadata,
            ),
            "case_context": lambda: _section(
                "case_context",
                "Contexto del expediente",
                _case_context_markdown(case),
                refs["case"],
                confidence=100,
            ),
            "relevant_facts": lambda: _section(
                "relevant_facts",
                "Hechos relevantes",
                _facts_markdown(source_payload),
                refs["analysis"] + refs["rules"][:5],
                confidence=_confidence(analysis),
            ),
            "labor_timeline": lambda: _section(
                "labor_timeline",
                "Linea de tiempo laboral",
                "La linea de tiempo laboral se deriva de la extraccion estructurada y del analisis completo disponible.",
                refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "applicable_regime": lambda: _section(
                "applicable_regime",
                "Regimen aplicable",
                _rules_markdown(source_payload["rules"][:3]),
                refs["rules"][:3] or refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "legal_rules": lambda: _section(
                "legal_rules",
                "Reglas aplicadas",
                ai_technical.content_markdown,
                refs["rules"] or refs["analysis"],
                confidence=ai_technical.confidence,
                ai_metadata=ai_technical.metadata,
            ),
            "calculation_summary": lambda: _section(
                "calculation_summary",
                "Resumen de calculo",
                _calculation_summary_markdown(source_payload),
                refs["calculations"] or refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "calculation_detail": lambda: _section(
                "calculation_detail",
                "Detalle de calculo",
                _calculation_detail_markdown(source_payload["calculations"]),
                refs["calculations"] or refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "inconsistency_matrix": lambda: _section(
                "inconsistency_matrix",
                "Matriz de inconsistencias",
                _inconsistency_matrix_markdown(source_payload["inconsistencies"]),
                refs["inconsistencies"] or refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "conclusions": lambda: _section(
                "conclusions",
                "Conclusiones",
                _conclusions_markdown(source_payload),
                refs["analysis"] + refs["rules"][:3] + refs["calculations"][:3],
                confidence=_confidence(analysis),
            ),
            "recommended_route": lambda: _section(
                "recommended_route",
                "Ruta recomendada",
                f"Ruta recomendada: **{analysis.recommended_route or 'revision_profesional'}**.",
                refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "missing_documents": lambda: _section(
                "missing_documents",
                "Documentos faltantes",
                _missing_documents_markdown(source_payload["inconsistencies"]),
                refs["inconsistencies"] or refs["analysis"],
                confidence=_confidence(analysis),
            ),
            "evidence_index": lambda: _section(
                "evidence_index",
                "Indice de evidencia",
                _evidence_markdown(source_payload),
                refs["analysis"] + refs["inconsistencies"],
                confidence=_confidence(analysis),
            ),
            "ai_disclaimer": lambda: _section(
                "ai_disclaimer",
                "Advertencia sobre IA",
                "El contenido fue preparado con apoyo de redaccion asistida sobre datos estructurados. No incorpora hechos, normas o valores por fuera de las fuentes registradas.",
                refs["analysis"],
                confidence=100,
            ),
            "professional_review_warning": lambda: _section(
                "professional_review_warning",
                "Revision profesional",
                "Este informe no reemplaza la validacion juridica profesional, especialmente si existe baja confianza, inconsistencias graves o documentos pendientes.",
                refs["analysis"],
                confidence=100,
            ),
            "appendix": lambda: _section(
                "appendix",
                "Anexos de trazabilidad",
                _appendix_markdown(source_payload),
                refs["analysis"] + refs["rules"] + refs["calculations"],
                confidence=100,
            ),
        }
        sections: list[dict[str, Any]] = []
        for key in section_keys:
            if key in builders:
                sections.append(builders[key]())
        for index, item in enumerate(sections, start=1):
            item["order_index"] = index
        _assert_supported_conclusions(sections)
        return sections

    def _source_payload(self, *, case: LaboraCase, analysis: FullAnalysis) -> dict[str, Any]:
        return _json_safe(
            {
                "case": {
                    "id": str(case.id),
                    "caseNumber": case.case_number,
                    "caseTypeRequested": case.case_type_requested,
                    "situationType": case.situation_type,
                    "pensionFundOrEntity": case.pension_fund_or_entity,
                },
                "analysis": {
                    "id": str(analysis.id),
                    "status": analysis.status,
                    "version": analysis.version,
                    "summary": analysis.summary or {},
                    "executiveResult": analysis.executive_result or {},
                    "legalConclusion": analysis.legal_conclusion,
                    "recommendedRoute": analysis.recommended_route,
                    "viabilityLevel": analysis.viability_level,
                    "confidenceGlobal": analysis.confidence_global,
                    "requiresHumanReview": analysis.requires_human_review,
                    "humanReviewReason": analysis.human_review_reason,
                    "completedAt": analysis.completed_at,
                },
                "rules": [self._rule_payload(item) for item in self._rules(analysis.id)],
                "calculations": [self._calculation_payload(item) for item in self._calculations(analysis.id)],
                "scenarios": [self._scenario_payload(item) for item in self._scenarios(analysis.id)],
                "inconsistencies": [self._inconsistency_payload(item) for item in self._inconsistencies(analysis.id)],
            }
        )

    def _rules(self, analysis_id: uuid.UUID) -> list[LegalRuleResult]:
        return (
            self.db.query(LegalRuleResult)
            .filter(LegalRuleResult.full_analysis_id == analysis_id)
            .order_by(LegalRuleResult.created_at.asc(), LegalRuleResult.rule_code.asc())
            .all()
        )

    def _calculations(self, analysis_id: uuid.UUID) -> list[CalculationResult]:
        return (
            self.db.query(CalculationResult)
            .filter(CalculationResult.full_analysis_id == analysis_id)
            .order_by(CalculationResult.created_at.asc(), CalculationResult.calculation_code.asc())
            .all()
        )

    def _scenarios(self, analysis_id: uuid.UUID) -> list[Scenario]:
        return (
            self.db.query(Scenario)
            .filter(Scenario.full_analysis_id == analysis_id)
            .order_by(Scenario.created_at.asc(), Scenario.scenario_type.asc())
            .all()
        )

    def _inconsistencies(self, analysis_id: uuid.UUID) -> list[AnalysisInconsistency]:
        return (
            self.db.query(AnalysisInconsistency)
            .filter(AnalysisInconsistency.full_analysis_id == analysis_id)
            .order_by(AnalysisInconsistency.created_at.asc(), AnalysisInconsistency.title.asc())
            .all()
        )

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
        if user.role in INTERNAL_ROLES:
            return True
        if case.owner_user_id == user.id:
            return True
        return self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        ) is not None

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if user.role in {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, "operator", "legal_ops", "reviewer"}:
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _require_review_role(self, user: User) -> None:
        if user.role in REVIEW_ROLES:
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REPORT_ACCESS_DENIED",
            message="No tienes permisos para revisar informes.",
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
            REPORT_EVENTS["access_denied"],
            actor=actor,
            case=case,
            report=None,
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="REPORT_ACCESS_DENIED",
            message="No tienes permisos para acceder a este informe o expediente.",
        )

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_report_or_404(self, report_id: str | uuid.UUID) -> Report:
        report = self.reports.get(report_id)
        if report is None or report.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_VERSION_NOT_FOUND",
                message="Informe no encontrado.",
            )
        return report

    def _requires_review(
        self,
        *,
        analysis: FullAnalysis,
        sections: list[dict[str, Any]],
    ) -> tuple[bool, str | None]:
        confidence = analysis.confidence_global
        if analysis.requires_human_review or analysis.status == "requires_review":
            return True, analysis.human_review_reason or "El analisis completo requiere revision humana."
        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            return True, "Confianza global inferior al umbral minimo."
        low_section = next(
            (item for item in sections if item.get("confidence") is not None and Decimal(str(item["confidence"])) < LOW_CONFIDENCE_THRESHOLD),
            None,
        )
        if low_section:
            return True, f"La seccion {low_section['section_key']} tiene baja confianza."
        return False, None

    def _title(self, case: LaboraCase, report_type: str) -> str:
        return f"{REPORT_TYPE_TITLES[report_type]} del expediente {case.case_number}"

    def _queued_payload(self, report: Report, job: ReportGenerationJob | None) -> dict[str, Any]:
        return {
            "reportId": str(report.id),
            "caseId": str(report.case_id),
            "status": report.status,
            "jobId": str(job.id) if job else None,
            "message": "La generacion del informe fue iniciada.",
        }

    def _ready_payload(self, report: Report) -> dict[str, Any]:
        return {
            "reportId": str(report.id),
            "caseId": str(report.case_id),
            "reportType": report.report_type,
            "status": report.status,
            "currentVersionId": str(report.current_version_id) if report.current_version_id else None,
            "requiresHumanReview": report.requires_human_review,
        }

    def _list_item(self, report: Report) -> dict[str, Any]:
        version = self.reports.current_version(report)
        return {
            "id": str(report.id),
            "caseId": str(report.case_id),
            "title": report.title,
            "reportType": report.report_type,
            "status": report.status,
            "currentVersionId": str(report.current_version_id) if report.current_version_id else None,
            "versionNumber": version.version_number if version else None,
            "requiresHumanReview": report.requires_human_review,
            "createdAt": report.created_at,
            "updatedAt": report.updated_at,
        }

    def _detail_payload(self, report: Report, version: ReportVersion | None) -> dict[str, Any]:
        sections = self.reports.list_sections(report.id)
        exports = self.reports.list_ready_exports(report.id)
        return {
            "id": str(report.id),
            "caseId": str(report.case_id),
            "title": report.title,
            "reportType": report.report_type,
            "status": report.status,
            "currentVersion": {
                "id": str(version.id),
                "versionNumber": version.version_number,
                "createdAt": version.created_at,
            }
            if version
            else None,
            "sections": [
                {
                    "id": str(item.id),
                    "sectionKey": item.section_key,
                    "title": item.title,
                    "contentMarkdown": item.content_markdown,
                    "contentJson": item.content_json,
                    "orderIndex": item.order_index,
                    "confidence": float(item.confidence) if item.confidence is not None else None,
                    "sourceRefs": item.source_refs or [],
                }
                for item in sections
            ],
            "availableExports": [
                {
                    "id": str(item.id),
                    "format": item.file_format,
                    "status": item.status,
                    "fileName": item.file_name,
                }
                for item in exports
            ],
            "traceability": {
                "contentHash": version.content_hash if version else None,
                "sourceHash": version.source_hash if version else None,
                "generatedAt": version.created_at if version else None,
            },
        }

    def _version_item(self, version: ReportVersion) -> dict[str, Any]:
        return {
            "id": str(version.id),
            "versionNumber": version.version_number,
            "status": version.status,
            "changeSummary": version.change_summary,
            "createdAt": version.created_at,
            "createdByRole": version.created_by_role,
        }

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
        event_type: str,
        *,
        actor: User | None,
        case: LaboraCase,
        report: Report | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_status: str | None = None,
        new_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_metadata = {
            "event": event_type,
            "actorUserId": str(actor.id) if actor else None,
            "actorRole": self._actor_role(actor),
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "reportId": str(report.id) if report else None,
            "previousStatus": previous_status,
            "newStatus": new_status,
            "sourceModule": "reports",
        }
        if metadata:
            event_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_type,
            entity_type="report",
            entity_id=report.id if report else None,
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
            return "support_agent" if user.role in {"support", "support_agent"} else "operator"
        if user.role == "system":
            return "system"
        return "user"

    def _rule_payload(self, item: LegalRuleResult) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "ruleCode": item.rule_code,
            "ruleName": item.rule_name,
            "ruleCategory": item.rule_category,
            "result": item.result,
            "resultDetail": item.result_detail or {},
            "explanation": item.explanation,
            "sourceRefs": item.source_refs or [],
            "confidence": item.confidence,
            "requiresReview": item.requires_review,
        }

    def _calculation_payload(self, item: CalculationResult) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "calculationCode": item.calculation_code,
            "calculationName": item.calculation_name,
            "calculationType": item.calculation_type,
            "inputValues": item.input_values or {},
            "formulaRef": item.formula_ref,
            "formulaExpression": item.formula_expression,
            "sourceRefs": item.source_refs or [],
            "resultValue": item.result_value,
            "resultUnit": item.result_unit,
            "resultDetail": item.result_detail or {},
            "confidence": item.confidence,
        }

    def _scenario_payload(self, item: Scenario) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "scenarioType": item.scenario_type,
            "name": item.name,
            "description": item.description,
            "amountEstimated": item.amount_estimated,
            "weeksEstimated": item.weeks_estimated,
            "retroactiveEstimated": item.retroactive_estimated,
            "differenceVsRecognized": item.difference_vs_recognized,
            "confidence": item.confidence,
        }

    def _inconsistency_payload(self, item: AnalysisInconsistency) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "type": item.inconsistency_type,
            "severity": item.severity,
            "title": item.title,
            "description": item.description,
            "evidenceRefs": item.evidence_refs or [],
            "legalRuleRefs": item.legal_rule_refs or [],
            "calculationRefs": item.calculation_refs or [],
            "economicImpactEstimated": item.economic_impact_estimated,
            "legalImpact": item.legal_impact,
            "missingDocuments": item.missing_documents or [],
            "recommendedAction": item.recommended_action,
            "confidence": item.confidence,
        }


class ReportExportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.reports = ReportRepository(db)
        self.report_service = ReportService(db)
        self.audit_events = AuditEventRepository(db)
        self.storage = DocumentStorageService()

    def request_export(
        self,
        report_id: str,
        payload: ReportExportRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        report = self.report_service._get_report_or_404(report_id)
        case = self.report_service._get_case_or_404(report.case_id)
        self.report_service._require_can_view(case, user, ip_address, user_agent)
        if report.status not in EXPORT_READY_REPORT_STATUSES:
            code = (
                "REPORT_LOW_CONFIDENCE_REQUIRES_REVIEW"
                if report.status == "requires_review"
                else "REPORT_FILE_NOT_READY"
            )
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code=code,
                message="El informe aun no esta listo para exportacion.",
                details={"reportId": str(report.id), "status": report.status},
            )
        version = self._version_for_export(report, payload.version_id)
        now = utc_now()
        export = self.reports.create_export(
            report_id=report.id,
            report_version_id=version.id,
            case_id=report.case_id,
            file_format=payload.format,
            status="queued",
            file_name=_export_file_name(report, version, payload.format),
            mime_type=_mime_type(payload.format),
            generated_by=user.id,
            created_at=now,
            updated_at=now,
        )
        self.report_service._audit(
            REPORT_EVENTS["export_requested"],
            actor=user,
            case=case,
            report=report,
            new_status=export.status,
            metadata={
                "exportFileId": str(export.id),
                "versionId": str(version.id),
                "format": payload.format,
                "includeTraceabilityStamp": payload.include_traceability_stamp,
                "includeEvidenceIndex": payload.include_evidence_index,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "exportFileId": str(export.id),
            "reportId": str(report.id),
            "versionId": str(version.id),
            "format": export.file_format,
            "status": export.status,
        }

    def run_export(
        self,
        export_file_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        export = self._get_export_or_404(export_file_id)
        report = self.report_service._get_report_or_404(export.report_id)
        case = self.report_service._get_case_or_404(report.case_id)
        version = self.reports.get_version(export.report_version_id)
        if version is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_VERSION_NOT_FOUND",
                message="Version de informe no encontrada.",
            )
        previous_status = export.status
        try:
            export.status = "generating"
            export.updated_at = utc_now()
            self.db.commit()
            content = _render_export_bytes(version.snapshot_markdown, export.file_format)
            checksum = hashlib.sha256(content).hexdigest()
            storage_key = f"reports/{export.id}.{export.file_format}"
            self.storage.save(
                storage_key=storage_key,
                content=content,
                content_type=export.mime_type,
            )
            export.storage_key = storage_key
            export.file_size_bytes = len(content)
            export.checksum_sha256 = checksum
            export.status = "ready"
            export.updated_at = utc_now()
            self.report_service._audit(
                REPORT_EVENTS["export_ready"],
                actor=actor,
                case=case,
                report=report,
                previous_status=previous_status,
                new_status=export.status,
                metadata={
                    "exportFileId": str(export.id),
                    "versionId": str(version.id),
                    "format": export.file_format,
                    "checksumSha256": checksum,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"exportFileId": str(export.id), "status": export.status}
        except Exception as exc:
            self.db.rollback()
            export = self._get_export_or_404(export_file_id)
            report = self.report_service._get_report_or_404(export.report_id)
            case = self.report_service._get_case_or_404(report.case_id)
            export.status = "failed"
            export.updated_at = utc_now()
            self.report_service._audit(
                REPORT_EVENTS["export_failed"],
                actor=actor,
                case=case,
                report=report,
                previous_status=previous_status,
                new_status=export.status,
                metadata={"exportFileId": str(export.id), "error": str(exc)[:500]},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"exportFileId": str(export.id), "status": export.status}

    def download_url(
        self,
        export_file_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        export = self._get_export_or_404(export_file_id)
        report = self.report_service._get_report_or_404(export.report_id)
        case = self.report_service._get_case_or_404(report.case_id)
        self.report_service._require_can_view(case, user, ip_address, user_agent)
        if export.status != "ready" or not export.storage_key:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_FILE_NOT_READY",
                message="La exportacion aun no esta lista para descarga.",
                details={"exportFileId": str(export.id), "status": export.status},
            )
        expires_at = utc_now() + timedelta(seconds=DOWNLOAD_TOKEN_TTL_SECONDS)
        expires = int(expires_at.timestamp())
        token = _download_signature(export.id, expires)
        download_url = (
            f"{settings.backend_public_url}{settings.api_v1_prefix}/exports/{export.id}/file"
            f"?expires={expires}&token={token}"
        )
        self.report_service._audit(
            REPORT_EVENTS["export_downloaded"],
            actor=user,
            case=case,
            report=report,
            metadata={"exportFileId": str(export.id), "format": export.file_format},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"downloadUrl": download_url, "expiresAt": expires_at}

    def stream_export(self, export_file_id: str, *, expires: int, token: str) -> StreamingResponse:
        export = self._get_export_or_404(export_file_id)
        if expires < int(time.time()) or not hmac.compare_digest(
            _download_signature(export.id, expires),
            token,
        ):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="REPORT_ACCESS_DENIED",
                message="El enlace de descarga no es valido o expiro.",
            )
        if export.status != "ready" or not export.storage_key:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_FILE_NOT_READY",
                message="La exportacion aun no esta lista para descarga.",
            )
        try:
            stream = self.storage.stream(export.storage_key)
        except (FileNotFoundError, StorageProviderError) as exc:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_FILE_NOT_READY",
                message="No se encontro el archivo exportado.",
            ) from exc
        quoted_name = quote(export.file_name)
        return StreamingResponse(
            stream,
            media_type=export.mime_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted_name}"},
        )

    def _version_for_export(self, report: Report, version_id: str | None) -> ReportVersion:
        if version_id:
            version = self.reports.get_version(version_id)
        else:
            version = self.reports.current_version(report)
        if version is None or version.report_id != report.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_VERSION_NOT_FOUND",
                message="Version de informe no encontrada.",
                details={"reportId": str(report.id), "versionId": version_id},
            )
        return version

    def _get_export_or_404(self, export_file_id: str | uuid.UUID) -> ExportFile:
        export = self.reports.get_export(export_file_id)
        if export is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="REPORT_VERSION_NOT_FOUND",
                message="Exportacion no encontrada.",
            )
        return export


def _selected_sections(report_type: str, include_sections: list[str]) -> list[str]:
    allowed = REPORT_TYPE_SECTIONS[report_type]
    if not include_sections:
        return allowed
    always = ["cover", "traceability_stamp", "ai_disclaimer"]
    requested = [key for key in include_sections if key in allowed]
    return [key for key in allowed if key in set(always + requested)]


def _section(
    key: str,
    title: str,
    markdown: str,
    refs: list[dict[str, Any]],
    *,
    confidence: Any,
    ai_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "section_key": key,
        "title": title,
        "content_markdown": markdown.strip() or "Sin informacion disponible.",
        "content_json": None,
        "order_index": SECTION_ORDER.index(key) + 1 if key in SECTION_ORDER else 999,
        "status": "ready",
        "confidence": Decimal(str(confidence)) if confidence is not None else None,
        "source_refs": refs or [],
        "ai_metadata": ai_metadata,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def _assert_supported_conclusions(sections: list[dict[str, Any]]) -> None:
    for item in sections:
        if item["section_key"] == "conclusions" and not item.get("source_refs"):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="REPORT_SOURCE_DATA_INCOMPLETE",
                message="La conclusion del informe no tiene soportes verificables.",
                details={"sectionKey": "conclusions"},
            )


def _case_context_markdown(case: LaboraCase) -> str:
    return "\n".join(
        [
            f"- Expediente: {case.case_number}",
            f"- Tipo solicitado: {case.case_type_requested}",
            f"- Situacion: {case.situation_type}",
            f"- Entidad/fondo: {case.pension_fund_or_entity or 'No registrado'}",
        ]
    )


def _facts_markdown(source: dict[str, Any]) -> str:
    rules = source.get("rules") or []
    if not rules:
        return "No hay hechos juridicos estructurados adicionales."
    return "\n".join(f"- {item['explanation']}" for item in rules[:6])


def _rules_markdown(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "No hay reglas aplicadas registradas."
    return "\n".join(
        f"- **{item['ruleName']}** (`{item['ruleCode']}`): {item['result']}."
        for item in rules
    )


def _calculation_summary_markdown(source: dict[str, Any]) -> str:
    calculations = source.get("calculations") or []
    scenarios = source.get("scenarios") or []
    lines = []
    if scenarios:
        lines.append("Escenarios:")
        lines.extend(
            f"- {item['name']}: monto {item.get('amountEstimated') or 'N/A'}, semanas {item.get('weeksEstimated') or 'N/A'}."
            for item in scenarios
        )
    if calculations:
        lines.append("Calculos principales:")
        lines.extend(
            f"- {item['calculationName']}: {item.get('resultValue')} {item.get('resultUnit') or ''}.".strip()
            for item in calculations[:5]
        )
    return "\n".join(lines) if lines else "No hay calculos registrados."


def _calculation_detail_markdown(calculations: list[dict[str, Any]]) -> str:
    if not calculations:
        return "No hay detalle de calculo disponible."
    lines = [
        "| Calculo | Resultado | Formula/regla | Confianza |",
        "| --- | ---: | --- | ---: |",
    ]
    for item in calculations:
        result = f"{item.get('resultValue')} {item.get('resultUnit') or ''}".strip()
        formula = item.get("formulaRef") or item.get("formulaExpression") or "No registrada"
        lines.append(f"| {item['calculationName']} | {result} | {formula} | {item.get('confidence')} |")
    return "\n".join(lines)


def _inconsistency_matrix_markdown(inconsistencies: list[dict[str, Any]]) -> str:
    if not inconsistencies:
        return "No se registraron inconsistencias en el analisis completo."
    lines = [
        "| Inconsistencia | Evidencia | Impacto juridico | Impacto economico | Documento faltante | Confianza |",
        "| --- | --- | --- | ---: | --- | ---: |",
    ]
    for item in inconsistencies:
        evidence = ", ".join(ref.get("label") or ref.get("id", "") for ref in item.get("evidenceRefs") or []) or "Ver fuente"
        missing = ", ".join(item.get("missingDocuments") or []) or "No aplica"
        lines.append(
            f"| {item['title']} | {evidence} | {item.get('legalImpact') or 'Por validar'} | "
            f"{item.get('economicImpactEstimated') or 'N/A'} | {missing} | {item.get('confidence')} |"
        )
    return "\n".join(lines)


def _conclusions_markdown(source: dict[str, Any]) -> str:
    analysis = source.get("analysis") or {}
    conclusion = analysis.get("legalConclusion") or "La conclusion depende de los datos estructurados disponibles."
    route = analysis.get("recommendedRoute") or "revision_profesional"
    confidence = analysis.get("confidenceGlobal") or "N/A"
    return f"{conclusion}\n\nRuta sugerida: **{route}**.\n\nNivel de confianza: **{confidence}**."


def _missing_documents_markdown(inconsistencies: list[dict[str, Any]]) -> str:
    missing: list[str] = []
    for item in inconsistencies:
        for document in item.get("missingDocuments") or []:
            if document and str(document) not in missing:
                missing.append(str(document))
    if not missing:
        return "No se registran documentos faltantes criticos."
    return "\n".join(f"- {document}" for document in missing)


def _evidence_markdown(source: dict[str, Any]) -> str:
    refs: list[str] = []
    for item in source.get("inconsistencies") or []:
        for ref in item.get("evidenceRefs") or []:
            label = ref.get("label") or ref.get("id")
            if label and label not in refs:
                refs.append(str(label))
    if not refs:
        return "La trazabilidad primaria esta en el analisis completo y sus datos estructurados."
    return "\n".join(f"- {ref}" for ref in refs)


def _appendix_markdown(source: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"- Analisis fuente: {source['analysis']['id']}",
            f"- Reglas aplicadas: {len(source.get('rules') or [])}",
            f"- Calculos: {len(source.get('calculations') or [])}",
            f"- Inconsistencias: {len(source.get('inconsistencies') or [])}",
            f"- Hash fuente: {_stable_hash(source)}",
        ]
    )


def _sections_markdown(sections: list[dict[str, Any]]) -> str:
    ordered = sorted(sections, key=lambda item: item["order_index"])
    return "\n\n".join(f"## {item['title']}\n\n{item['content_markdown']}" for item in ordered)


def _confidence(analysis: FullAnalysis) -> Decimal:
    return analysis.confidence_global or Decimal("80.00")


def _first_ai_metadata(sections: list[dict[str, Any]], key: str) -> str | None:
    for item in sections:
        metadata = item.get("ai_metadata") or {}
        if metadata.get(key):
            return str(metadata[key])
    return None


def _export_file_name(report: Report, version: ReportVersion, fmt: str) -> str:
    title = _slug(report.title)
    return f"{title}-v{version.version_number}.{fmt}"


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "informe"


def _mime_type(fmt: str) -> str:
    if fmt == "pdf":
        return "application/pdf"
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _render_export_bytes(markdown: str, fmt: str) -> bytes:
    text = _markdown_to_plain_text(markdown)
    if fmt == "pdf":
        return _simple_pdf(text)
    if fmt == "docx":
        return _simple_docx(text)
    raise ValueError("Formato no soportado.")


def _markdown_to_plain_text(markdown: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", markdown)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", " | ", text)
    return text.strip()


def _simple_pdf(text: str) -> bytes:
    lines = _wrap_lines(text, width=88)[:48]
    content_lines = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
    for line in lines:
        content_lines.append(f"({_pdf_escape(line)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{idx} 0 obj\n".encode("ascii"))
        pdf.write(obj)
        pdf.write(b"\nendobj\n")
    xref_at = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return pdf.getvalue()


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _simple_docx(text: str) -> bytes:
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{xml_escape(line)}</w:t></w:r></w:p>"
        for line in text.splitlines()
        if line.strip()
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _wrap_lines(text: str, *, width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
    return lines


def _download_signature(export_id: uuid.UUID, expires: int) -> str:
    payload = f"report-export:{export_id}:{expires}".encode("utf-8")
    return hmac.new(settings.jwt_access_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
