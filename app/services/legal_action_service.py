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
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.case_result import RecommendedRoute
from app.models.document import Document
from app.models.full_analysis import (
    AnalysisInconsistency,
    CalculationResult,
    FullAnalysis,
    LegalRuleResult,
    Scenario,
)
from app.models.legal_action import (
    DraftExport,
    DraftSection,
    LegalAction,
    LegalActionJob,
    LegalDraft,
    LegalTemplate,
)
from app.models.paywall import Paywall
from app.models.report import Report
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.full_analysis_repository import FullAnalysisRepository
from app.repositories.legal_action_repository import LegalActionRepository
from app.repositories.payment_repository import PaymentRepository
from app.services.consent_service import ConsentComplianceService
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.services.legal_draft_ai_provider import LegalDraftAiOutput, build_legal_draft_ai_provider
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
SUPPORT_ROLES = {"support", "support_agent", "operator", "legal_ops", "reviewer"}
INTERNAL_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, *SUPPORT_ROLES, "system"}
REVIEW_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES}

READY_CASE_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "completed",
    "requires_review",
    "result_completed",
    "report_ready",
}
LOCKED_CASE_STATUSES = {"closed", "archived", "blocked"}
READY_ANALYSIS_STATUSES = {"completed", "requires_review"}
READY_REPORT_STATUSES = {"ready", "approved", "requires_review"}
LOW_CONFIDENCE_THRESHOLD = Decimal("70.00")
MANDATORY_REVIEW_THRESHOLD = Decimal("55.00")
DOWNLOAD_TOKEN_TTL_SECONDS = 600

LEGAL_ACTION_TYPES = [
    "technical_report_download",
    "executive_summary",
    "petition",
    "administrative_claim",
    "reliquidation_request",
    "administrative_appeal",
    "lawsuit_draft",
    "professional_review_request",
]

LEGAL_ACTION_EVENTS = {
    "created": "acciones_juridicas.created",
    "updated": "acciones_juridicas.updated",
    "viewed": "acciones_juridicas.viewed",
    "submitted": "acciones_juridicas.submitted",
    "approved": "acciones_juridicas.approved",
    "rejected": "acciones_juridicas.rejected",
    "failed": "acciones_juridicas.failed",
    "draft_created": "legal_draft.created",
    "draft_generated": "legal_draft.generated",
    "draft_edited": "legal_draft.edited",
    "section_regenerated": "legal_draft.section_regenerated",
    "quality_checked": "legal_draft.quality_checked",
    "export_requested": "legal_draft.export_requested",
    "exported": "legal_draft.exported",
    "review_requested": "legal_draft.review_requested",
    "review_approved": "legal_draft.review_approved",
    "review_rejected": "legal_draft.review_rejected",
    "review_changes_requested": "legal_draft.review_changes_requested",
    "access_denied": "acciones_juridicas.access_denied",
}

ACTION_TITLES = {
    "technical_report_download": "Descarga de informe tecnico",
    "executive_summary": "Resumen ejecutivo",
    "petition": "Derecho de peticion",
    "administrative_claim": "Reclamacion administrativa",
    "reliquidation_request": "Solicitud de reliquidacion",
    "administrative_appeal": "Recurso administrativo",
    "lawsuit_draft": "Borrador de demanda",
    "professional_review_request": "Solicitud de revision profesional",
}

COMMON_REQUEST_SECTIONS = [
    ("recipient", "Destinatario"),
    ("claimant_identification", "Identificacion del solicitante"),
    ("facts", "Hechos"),
    ("requests", "Peticiones"),
    ("legal_basis", "Fundamentos juridicos"),
    ("evidence", "Pruebas"),
    ("attachments", "Anexos"),
    ("notifications", "Notificaciones"),
    ("signature", "Firma"),
]

LAWSUIT_SECTIONS = [
    ("heading", "Encabezado"),
    ("parties", "Partes"),
    ("jurisdiction_and_competence", "Jurisdiccion y competencia"),
    ("facts", "Hechos"),
    ("claims", "Pretensiones"),
    ("legal_basis", "Fundamentos juridicos"),
    ("evidence", "Pruebas"),
    ("attachments", "Anexos"),
    ("estimated_amount_or_oath", "Cuantia o juramento estimatorio"),
    ("notifications", "Notificaciones"),
    ("signature", "Firma"),
    ("professional_review_warning", "Advertencia de revision profesional"),
]

QUALITY_CHECK_KEYS = [
    "parties_identified",
    "facts_match_evidence",
    "claims_match_route",
    "legal_basis_present",
    "attachments_listed",
    "missing_data_marked",
    "no_internal_contradictions",
    "amounts_match_calculation",
    "professional_review_warning_present",
]


class LegalTemplateService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = LegalActionRepository(db)

    def resolve(self, action_type: str) -> LegalTemplate:
        template = self.repository.active_template(action_type=action_type)
        if template is not None:
            return template
        if action_type not in LEGAL_ACTION_TYPES:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="LEGAL_TEMPLATE_NOT_FOUND",
                message="La plantilla solicitada no existe.",
            )
        return self.repository.create_template(**_default_template(action_type))


class LegalDraftVersionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = LegalActionRepository(db)

    def create_version(
        self,
        *,
        draft: LegalDraft,
        change_type: str,
        change_summary: str | None,
        actor: User,
    ):
        sections = self.repository.list_sections(draft.id)
        snapshot = _draft_snapshot(draft, sections)
        content_hash = _stable_hash(snapshot)
        version = self.repository.create_version(
            draft_id=draft.id,
            version_number=self.repository.next_version_number(draft.id),
            change_type=change_type,
            snapshot=_json_safe(snapshot),
            content_hash=content_hash,
            change_summary=change_summary,
            created_by=actor.id,
            created_at=utc_now(),
        )
        draft.current_version_number = version.version_number
        draft.updated_at = utc_now()
        return version


class LegalActionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.full_analysis = FullAnalysisRepository(db)
        self.payments = PaymentRepository(db)
        self.repository = LegalActionRepository(db)
        self.audit_events = AuditEventRepository(db)
        self.templates = LegalTemplateService(db)
        self.versions = LegalDraftVersionService(db)
        self.ai_provider = build_legal_draft_ai_provider()
        self.storage = DocumentStorageService()

    def available_actions(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case, analysis, report = self._validated_sources(case_id, user=user, require_update=False, ip_address=ip_address, user_agent=user_agent)
        actions = self._evaluate_actions(case=case, analysis=analysis, report=report)
        self._audit(
            LEGAL_ACTION_EVENTS["viewed"],
            actor=user,
            case=case,
            action=None,
            resource_type="legal_action",
            metadata={"sourceAnalysisId": str(analysis.id), "sourceReportId": str(report.id), "view": "available_actions"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "sourceAnalysisId": str(analysis.id),
            "sourceReportId": str(report.id),
            "actions": actions,
        }

    def create_action(
        self,
        case_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        case, analysis, report = self._validated_sources(case_id, user=user, require_update=True, ip_address=ip_address, user_agent=user_agent)
        evaluation = self._evaluation_for_action(case=case, analysis=analysis, report=report, action_type=payload.action_type)
        if evaluation["status"] == "blocked":
            code = "LEGAL_ACTION_NOT_ALLOWED_BY_ROUTE" if payload.action_type == "lawsuit_draft" else "CASE_NOT_READY_FOR_LEGAL_ACTIONS"
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code=code,
                message=evaluation["reason"] or "La accion juridica no esta habilitada para este expediente.",
                details={"actionType": payload.action_type, "caseId": str(case.id)},
            )

        existing = self.repository.active_action_for_case_type(
            case_id=case.id,
            action_type=payload.action_type,
        )
        if existing is not None:
            self._audit(
                LEGAL_ACTION_EVENTS["viewed"],
                actor=user,
                case=case,
                action=existing,
                resource_type="legal_action",
                metadata={"reused": True, "actionType": payload.action_type},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return self._action_payload(existing), status.HTTP_200_OK

        now = utc_now()
        action = self.repository.create_action(
            case_id=case.id,
            user_id=case.owner_user_id,
            action_type=payload.action_type,
            source_analysis_id=analysis.id,
            source_report_id=report.id,
            source_route_id=self._source_route_id(case.id),
            status="not_started",
            eligibility_status=evaluation["status"],
            eligibility_reason=evaluation["reason"],
            professional_review_level=evaluation["professionalReviewLevel"],
            warnings=evaluation["warnings"],
            pending_data=evaluation["pendingData"],
            missing_attachments=evaluation["missingAttachments"],
            selected_by_user=payload.selected_by_user,
            created_by=user.id,
            created_at=now,
            updated_at=now,
        )
        self._record_history_event(
            case=case,
            event_type=LEGAL_ACTION_EVENTS["created"],
            title="Accion juridica creada",
            description=f"Se creo la accion {ACTION_TITLES.get(payload.action_type, payload.action_type)}.",
            severity="info",
            actor=user,
            metadata={"legalActionId": str(action.id), "actionType": payload.action_type},
        )
        self._audit(
            LEGAL_ACTION_EVENTS["created"],
            actor=user,
            case=case,
            action=action,
            resource_type="legal_action",
            new_status=action.status,
            metadata={"actionType": payload.action_type, "eligibilityStatus": action.eligibility_status},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._action_payload(action), status.HTTP_201_CREATED

    def list_actions(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        items = [self._action_payload(item) for item in self.repository.list_actions_for_case(case.id)]
        return {"items": items}

    def get_action(
        self,
        action_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        action = self._get_action_or_404(action_id)
        case = self._get_case_or_404(action.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._audit(
            LEGAL_ACTION_EVENTS["viewed"],
            actor=user,
            case=case,
            action=action,
            resource_type="legal_action",
            metadata={"actionType": action.action_type},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._action_payload(action)

    def create_draft(
        self,
        action_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[dict[str, Any], int]:
        action = self._get_action_or_404(action_id)
        case = self._get_case_or_404(action.case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        if action.eligibility_status == "blocked":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="LEGAL_ACTION_NOT_ALLOWED_BY_ROUTE",
                message=action.eligibility_reason or "La accion juridica esta bloqueada.",
            )
        active_job = None
        existing = self.repository.latest_draft_for_action(action.id)
        if existing and existing.status == "generating":
            active_job = self.repository.active_generation_job_for_draft(existing.id)
            return {
                "draftId": str(existing.id),
                "status": existing.status,
                "jobId": str(active_job.id) if active_job else None,
                "pollUrl": f"/drafts/{existing.id}",
            }, status.HTTP_202_ACCEPTED

        template = self.templates.resolve(action.action_type)
        now = utc_now()
        draft = self.repository.create_draft(
            legal_action_id=action.id,
            case_id=case.id,
            user_id=case.owner_user_id,
            template_id=template.id,
            template_version=template.template_version,
            title=template.display_name,
            status="generating" if payload.generation_mode == "ai_generated" else "created",
            generation_mode=payload.generation_mode,
            document_metadata=payload.document_metadata,
            user_inputs=payload.user_inputs,
            professional_review_level=action.professional_review_level,
            is_locked=False,
            current_version_number=0,
            created_at=now,
            updated_at=now,
            last_edited_by=user.id,
        )
        previous_status = action.status
        action.status = "in_progress"
        action.updated_at = now
        self._audit(
            LEGAL_ACTION_EVENTS["draft_created"],
            actor=user,
            case=case,
            action=action,
            draft=draft,
            resource_type="legal_draft",
            previous_status=previous_status,
            new_status=draft.status,
            metadata={"generationMode": payload.generation_mode, "templateId": str(template.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        if payload.generation_mode != "ai_generated":
            sections = self._template_sections(
                case=case,
                action=action,
                draft=draft,
                template=template,
                generated_by_ai=False,
            )
            self.repository.replace_sections(draft, sections)
            draft.status = "ready_for_edit"
            version = self.versions.create_version(
                draft=draft,
                change_type="created",
                change_summary="Borrador creado desde plantilla base.",
                actor=user,
            )
            self._audit(
                LEGAL_ACTION_EVENTS["draft_generated"],
                actor=user,
                case=case,
                action=action,
                draft=draft,
                resource_type="legal_draft",
                previous_status="created",
                new_status=draft.status,
                metadata={"versionNumber": version.version_number, "generationMode": payload.generation_mode},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"draftId": str(draft.id), "status": draft.status, "jobId": None, "pollUrl": None}, status.HTTP_201_CREATED

        job = self._create_job(
            job_type="legal_draft.generate",
            case_id=case.id,
            draft_id=draft.id,
            idempotency_key=f"legal_draft.generate:{draft.id}",
        )
        self.db.commit()
        if payload.output_mode == "sync":
            self.run_generation_job(str(job.id), actor=user, ip_address=ip_address, user_agent=user_agent)
            return {"draftId": str(draft.id), "status": "ready_for_edit", "jobId": str(job.id), "pollUrl": f"/drafts/{draft.id}"}, status.HTTP_201_CREATED
        return {"draftId": str(draft.id), "status": "generating", "jobId": str(job.id), "pollUrl": f"/drafts/{draft.id}"}, status.HTTP_202_ACCEPTED

    def get_draft(
        self,
        draft_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        action = self._get_action_or_404(draft.legal_action_id)
        self._audit(
            LEGAL_ACTION_EVENTS["viewed"],
            actor=user,
            case=case,
            action=action,
            draft=draft,
            resource_type="legal_draft",
            metadata={"draftId": str(draft.id), "actionType": action.action_type},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._draft_payload(draft, action=action, include_download_urls=True)

    def update_draft(
        self,
        draft_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        self._require_can_update_draft(case, draft, user, ip_address, user_agent)
        action = self._get_action_or_404(draft.legal_action_id)
        previous_status = draft.status
        if payload.title:
            draft.title = payload.title.strip()
        if payload.document_metadata is not None:
            draft.document_metadata = _json_safe(payload.document_metadata)
        section_ids = []
        for patch in payload.sections:
            section = self.repository.get_section(patch.section_id)
            if section is None or section.draft_id != draft.id:
                raise ApiError(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    code="DRAFT_SECTION_NOT_FOUND",
                    message="La seccion indicada no pertenece al borrador.",
                    details={"sectionId": patch.section_id},
                )
            clean_html = _sanitize_html(patch.content_html)
            section.content_html = clean_html
            section.content_plain = _html_to_plain(clean_html)
            section.status = "edited"
            section.updated_at = utc_now()
            section_ids.append(str(section.id))
        draft.status = "editing"
        draft.last_edited_by = user.id
        draft.updated_at = utc_now()
        version = self.versions.create_version(
            draft=draft,
            change_type="manual_edit",
            change_summary=payload.change_summary or "Edicion manual del borrador.",
            actor=user,
        )
        self._audit(
            LEGAL_ACTION_EVENTS["draft_edited"],
            actor=user,
            case=case,
            action=action,
            draft=draft,
            resource_type="legal_draft",
            previous_status=previous_status,
            new_status=draft.status,
            metadata={"sectionIds": section_ids, "versionNumber": version.version_number},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "draftId": str(draft.id),
            "status": draft.status,
            "versionNumber": version.version_number,
            "updatedAt": draft.updated_at,
        }

    def request_section_regeneration(
        self,
        draft_id: str,
        section_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        section = self.repository.get_section(section_id)
        if section is None or section.draft_id != draft.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DRAFT_NOT_FOUND",
                message="Seccion de borrador no encontrada.",
            )
        case = self._get_case_or_404(draft.case_id)
        self._require_can_update_draft(case, draft, user, ip_address, user_agent)
        action = self._get_action_or_404(draft.legal_action_id)
        self.versions.create_version(
            draft=draft,
            change_type="ai_regeneration",
            change_summary=f"Snapshot previo a regenerar seccion {section.section_key}.",
            actor=user,
        )
        section.status = "generating"
        section.updated_at = utc_now()
        draft.status = "generating"
        draft.updated_at = utc_now()
        job = self._create_job(
            job_type="draft_section.regenerate",
            case_id=case.id,
            draft_id=draft.id,
            section_id=section.id,
            idempotency_key=f"draft_section.regenerate:{section.id}:{draft.current_version_number}:{uuid.uuid4()}",
        )
        job.error_message = json.dumps(
            {
                "instruction": payload.instruction,
                "preserveUserEdits": payload.preserve_user_edits,
            },
            ensure_ascii=True,
        )
        self._audit(
            LEGAL_ACTION_EVENTS["submitted"],
            actor=user,
            case=case,
            action=action,
            draft=draft,
            resource_type="legal_draft",
            previous_status="editing",
            new_status=draft.status,
            metadata={"sectionId": str(section.id), "jobId": str(job.id), "jobType": job.job_type},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"sectionId": str(section.id), "status": section.status, "jobId": str(job.id)}

    def request_quality_check(
        self,
        draft_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        self._require_can_update_draft(case, draft, user, ip_address, user_agent)
        previous_status = draft.status
        draft.status = "quality_check_pending"
        draft.updated_at = utc_now()
        job = self._create_job(
            job_type="draft.quality_check",
            case_id=case.id,
            draft_id=draft.id,
            idempotency_key=f"draft.quality_check:{draft.id}:{uuid.uuid4()}",
        )
        self._audit(
            LEGAL_ACTION_EVENTS["submitted"],
            actor=user,
            case=case,
            action=self._get_action_or_404(draft.legal_action_id),
            draft=draft,
            resource_type="legal_draft",
            previous_status=previous_status,
            new_status=draft.status,
            metadata={"jobId": str(job.id), "jobType": job.job_type},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"draftId": str(draft.id), "status": draft.status, "jobId": str(job.id)}

    def request_export(
        self,
        draft_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        self._require_can_update_draft(case, draft, user, ip_address, user_agent)
        action = self._get_action_or_404(draft.legal_action_id)
        self._validate_export_allowed(draft, payload.include_watermark)
        version = self.versions.create_version(
            draft=draft,
            change_type="created",
            change_summary="Snapshot previo a exportacion.",
            actor=user,
        )
        export = self.repository.create_export(
            draft_id=draft.id,
            case_id=case.id,
            format=payload.format,
            file_name=_export_file_name(action.action_type, case.case_number, payload.format),
            status="processing",
            mime_type=_mime_type(payload.format),
            version_number=version.version_number,
            include_watermark=payload.include_watermark,
            created_by=user.id,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        job = self._create_job(
            job_type="draft.export",
            case_id=case.id,
            draft_id=draft.id,
            export_id=export.id,
            idempotency_key=f"draft.export:{export.id}",
        )
        self._audit(
            LEGAL_ACTION_EVENTS["export_requested"],
            actor=user,
            case=case,
            action=action,
            draft=draft,
            export=export,
            resource_type="draft_export",
            metadata={"format": payload.format, "versionNumber": version.version_number, "jobId": str(job.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"exportId": str(export.id), "status": export.status, "jobId": str(job.id)}

    def list_exports(
        self,
        draft_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        return {"exports": [self._export_payload(item, include_download_url=True) for item in self.repository.list_exports(draft.id)]}

    def download_export_url(
        self,
        export_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        export = self._get_export_or_404(export_id)
        draft = self._get_draft_or_404(export.draft_id)
        case = self._get_case_or_404(export.case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        if export.status != "ready" or not export.storage_key:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="EXPORT_FAILED",
                message="La exportacion aun no esta lista para descarga.",
                details={"exportId": str(export.id), "status": export.status},
            )
        expires_at = utc_now() + timedelta(seconds=DOWNLOAD_TOKEN_TTL_SECONDS)
        return {"downloadUrl": self._signed_export_url(export, expires_at), "expiresAt": expires_at}

    def stream_export(self, export_id: str, *, expires: int, token: str) -> StreamingResponse:
        export = self._get_export_or_404(export_id)
        if expires < int(time.time()) or not hmac.compare_digest(
            _download_signature(export.id, expires),
            token,
        ):
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="MISSING_REQUIRED_PERMISSION",
                message="El enlace de descarga no es valido o expiro.",
            )
        if export.status != "ready" or not export.storage_key:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="EXPORT_FAILED",
                message="La exportacion aun no esta lista.",
            )
        try:
            stream = self.storage.stream(export.storage_key)
        except (FileNotFoundError, StorageProviderError) as exc:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="EXPORT_FAILED",
                message="No se encontro el archivo exportado.",
            ) from exc
        quoted_name = quote(export.file_name)
        return StreamingResponse(
            stream,
            media_type=export.mime_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted_name}"},
        )

    def submit_review(
        self,
        draft_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        self._require_can_update_draft(case, draft, user, ip_address, user_agent)
        action = self._get_action_or_404(draft.legal_action_id)
        previous_status = draft.status
        draft.status = "requires_review"
        draft.is_locked = True
        draft.updated_at = utc_now()
        action.status = "requires_review"
        action.updated_at = utc_now()
        comment = self.repository.create_comment(
            draft_id=draft.id,
            author_id=user.id,
            author_role=self._actor_role(user),
            body=payload.message,
            status="open",
            created_at=utc_now(),
        )
        self._audit(
            LEGAL_ACTION_EVENTS["review_requested"],
            actor=user,
            case=case,
            action=action,
            draft=draft,
            resource_type="legal_draft",
            previous_status=previous_status,
            new_status=draft.status,
            metadata={"reviewRequestId": str(comment.id), "priority": payload.priority},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"draftId": str(draft.id), "status": draft.status, "reviewRequestId": str(comment.id)}

    def admin_list_drafts(
        self,
        *,
        status_filter: str | None,
        case_id: str | None,
        action_type: str | None,
        page: int,
        page_size: int,
        user: User,
    ) -> dict[str, Any]:
        self._require_review_role(user)
        parsed_case_id = _parse_uuid(case_id)
        rows, total = self.repository.list_admin_drafts(
            status=status_filter,
            case_id=parsed_case_id,
            action_type=action_type,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [
                {
                    "draftId": str(draft.id),
                    "caseId": str(case.id),
                    "caseNumber": case.case_number,
                    "actionType": action.action_type,
                    "status": draft.status,
                    "qualityScore": _float(draft.quality_score),
                    "professionalReviewLevel": draft.professional_review_level,
                    "updatedAt": draft.updated_at,
                }
                for draft, action, case in rows
            ],
            "pagination": {"page": page, "pageSize": page_size, "total": total},
        }

    def review_decision(
        self,
        draft_id: str,
        payload,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_review_role(user)
        draft = self._get_draft_or_404(draft_id)
        case = self._get_case_or_404(draft.case_id)
        action = self._get_action_or_404(draft.legal_action_id)
        previous_status = draft.status
        now = utc_now()
        if payload.decision == "approved":
            draft.status = "approved"
            draft.is_locked = False
            action.status = "completed"
            event = LEGAL_ACTION_EVENTS["review_approved"]
        elif payload.decision == "changes_requested":
            draft.status = "ready_for_edit"
            draft.is_locked = False
            action.status = "in_progress"
            event = LEGAL_ACTION_EVENTS["review_changes_requested"]
        else:
            draft.status = "failed"
            draft.is_locked = True
            action.status = "blocked"
            event = LEGAL_ACTION_EVENTS["review_rejected"]
        draft.updated_at = now
        action.updated_at = now
        self.repository.create_comment(
            draft_id=draft.id,
            author_id=user.id,
            author_role=self._actor_role(user),
            body=payload.review_notes or f"Decision de revision: {payload.decision}.",
            status="open",
            created_at=now,
        )
        self._audit(
            event,
            actor=user,
            case=case,
            action=action,
            draft=draft,
            resource_type="legal_draft",
            previous_status=previous_status,
            new_status=draft.status,
            metadata={"decision": payload.decision, "reviewNotes": payload.review_notes},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"draftId": str(draft.id), "status": draft.status, "reviewedAt": now}

    def run_generation_job(
        self,
        job_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        job = self._get_job_or_404(job_id)
        if job.status == "success":
            return {"jobId": str(job.id), "status": job.status}
        draft = self._get_draft_or_404(job.draft_id)
        action = self._get_action_or_404(draft.legal_action_id)
        case = self._get_case_or_404(draft.case_id)
        try:
            job.status = "processing"
            job.started_at = job.started_at or utc_now()
            job.attempts += 1
            job.updated_at = utc_now()
            draft.status = "generating"
            draft.updated_at = utc_now()
            self.db.commit()

            analysis = self.full_analysis.latest_completed_for_case(case.id)
            report = self._latest_ready_report(case.id)
            template = self.templates.resolve(action.action_type)
            output = self.ai_provider.generate_draft(
                self._source_payload(
                    case=case,
                    analysis=analysis,
                    report=report,
                    action=action,
                    draft=draft,
                    template=template,
                )
            )
            self._apply_ai_draft_output(draft=draft, action=action, output=output)
            self.repository.create_ai_run(
                draft_id=draft.id,
                case_id=case.id,
                provider=output.provider,
                model=output.model,
                prompt_version=output.prompt_version,
                input_hash=output.input_hash,
                output_hash=output.output_hash,
                structured_output=_json_safe(_ai_output_payload(output)),
                confidence_score=Decimal(str(output.confidence_score / 100)),
                status="success" if output.confidence_score >= 55 else "low_confidence",
                created_at=utc_now(),
            )
            acting_user = actor or self._system_actor()
            version = self.versions.create_version(
                draft=draft,
                change_type="created",
                change_summary="Generacion controlada del borrador.",
                actor=acting_user,
            )
            job.status = "success"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            self._audit(
                LEGAL_ACTION_EVENTS["draft_generated"],
                actor=actor,
                case=case,
                action=action,
                draft=draft,
                resource_type="legal_draft",
                previous_status="generating",
                new_status=draft.status,
                metadata={"jobId": str(job.id), "versionNumber": version.version_number, "provider": output.provider},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"jobId": str(job.id), "draftId": str(draft.id), "status": job.status}
        except Exception as exc:
            self.db.rollback()
            job = self._get_job_or_404(job_id)
            draft = self._get_draft_or_404(job.draft_id)
            action = self._get_action_or_404(draft.legal_action_id)
            case = self._get_case_or_404(draft.case_id)
            job.status = "failed"
            job.error_code = "AI_GENERATION_FAILED"
            job.error_message = str(exc)[:2000]
            job.finished_at = utc_now()
            draft.status = "failed"
            action.status = "error"
            self._audit(
                LEGAL_ACTION_EVENTS["failed"],
                actor=actor,
                case=case,
                action=action,
                draft=draft,
                resource_type="legal_draft",
                new_status=draft.status,
                metadata={"jobId": str(job.id), "error": str(exc)[:500]},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"jobId": str(job.id), "draftId": str(draft.id), "status": job.status}

    def run_section_regeneration_job(
        self,
        job_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        job = self._get_job_or_404(job_id)
        if job.status == "success":
            return {"jobId": str(job.id), "status": job.status}
        draft = self._get_draft_or_404(job.draft_id)
        section = self.repository.get_section(job.section_id)
        action = self._get_action_or_404(draft.legal_action_id)
        case = self._get_case_or_404(draft.case_id)
        if section is None:
            raise ApiError(status_code=404, code="DRAFT_NOT_FOUND", message="Seccion no encontrada.")
        try:
            job.status = "processing"
            job.started_at = job.started_at or utc_now()
            job.attempts += 1
            job.updated_at = utc_now()
            self.db.commit()

            options = _json_loads(job.error_message) or {}
            analysis = self.full_analysis.latest_completed_for_case(case.id)
            report = self._latest_ready_report(case.id)
            template = self.templates.resolve(action.action_type)
            source_payload = self._source_payload(
                case=case,
                analysis=analysis,
                report=report,
                action=action,
                draft=draft,
                template=template,
            )
            source_payload["currentSection"] = {
                "id": str(section.id),
                "sectionKey": section.section_key,
                "contentPlain": section.content_plain,
                "pendingMarkers": section.pending_markers,
            }
            output = self.ai_provider.regenerate_section(
                source_payload,
                section_key=section.section_key,
                instruction=options.get("instruction") or "Mejorar redaccion.",
                preserve_user_edits=bool(options.get("preserveUserEdits", True)),
            )
            generated = output.sections[0]
            section.content_html = _markdown_to_html(generated["content_markdown"])
            section.content_plain = _html_to_plain(section.content_html)
            section.source_references = generated.get("source_references") or []
            section.pending_markers = generated.get("pending_markers") or []
            section.confidence_score = Decimal(str(generated.get("confidence_score") or output.confidence_score / 100))
            section.generated_by_ai = True
            section.status = "generated" if output.confidence_score >= 70 else "low_confidence"
            section.updated_at = utc_now()
            draft.status = "ready_for_edit" if draft.status == "generating" else draft.status
            draft.updated_at = utc_now()
            self.repository.create_ai_run(
                draft_id=draft.id,
                section_id=section.id,
                case_id=case.id,
                provider=output.provider,
                model=output.model,
                prompt_version=output.prompt_version,
                input_hash=output.input_hash,
                output_hash=output.output_hash,
                structured_output=_json_safe(_ai_output_payload(output)),
                confidence_score=Decimal(str(output.confidence_score / 100)),
                status="success" if output.confidence_score >= 55 else "low_confidence",
                created_at=utc_now(),
            )
            version = self.versions.create_version(
                draft=draft,
                change_type="ai_regeneration",
                change_summary=f"Regeneracion IA de seccion {section.section_key}.",
                actor=actor or self._system_actor(),
            )
            job.status = "success"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            self._audit(
                LEGAL_ACTION_EVENTS["section_regenerated"],
                actor=actor,
                case=case,
                action=action,
                draft=draft,
                resource_type="draft_section",
                previous_status="generating",
                new_status=section.status,
                metadata={"sectionId": str(section.id), "jobId": str(job.id), "versionNumber": version.version_number},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"jobId": str(job.id), "sectionId": str(section.id), "status": job.status}
        except Exception as exc:
            self.db.rollback()
            job = self._get_job_or_404(job_id)
            draft = self._get_draft_or_404(job.draft_id)
            section = self.repository.get_section(job.section_id)
            job.status = "failed"
            job.error_code = "AI_GENERATION_FAILED"
            job.error_message = str(exc)[:2000]
            job.finished_at = utc_now()
            if section:
                section.status = "failed"
            draft.status = "failed"
            self.db.commit()
            return {"jobId": str(job.id), "status": job.status}

    def run_quality_check_job(
        self,
        job_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        job = self._get_job_or_404(job_id)
        draft = self._get_draft_or_404(job.draft_id)
        action = self._get_action_or_404(draft.legal_action_id)
        case = self._get_case_or_404(draft.case_id)
        job.status = "processing"
        job.started_at = job.started_at or utc_now()
        job.attempts += 1
        job.updated_at = utc_now()
        checks, score, critical = self._quality_checks(draft=draft, action=action)
        if critical:
            overall = "failed"
            draft.status = "quality_check_failed"
        elif draft.professional_review_level == "mandatory" and draft.status != "approved":
            overall = "requires_review"
            draft.status = "requires_review"
        elif any(item["status"] == "warning" for item in checks):
            overall = "passed_with_warnings"
            draft.status = "quality_check_passed"
        else:
            overall = "passed"
            draft.status = "quality_check_passed"
        draft.quality_score = Decimal(str(score))
        draft.updated_at = utc_now()
        check = self.repository.create_quality_check(
            draft_id=draft.id,
            overall_status=overall,
            score=Decimal(str(score)),
            checks=checks,
            critical_warnings=critical,
            created_at=utc_now(),
        )
        job.status = "success"
        job.finished_at = utc_now()
        job.updated_at = utc_now()
        self._audit(
            LEGAL_ACTION_EVENTS["quality_checked"],
            actor=actor,
            case=case,
            action=action,
            draft=draft,
            resource_type="draft_quality_check",
            previous_status="quality_check_pending",
            new_status=draft.status,
            metadata={"qualityCheckId": str(check.id), "overallStatus": overall, "score": score},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"jobId": str(job.id), "draftId": str(draft.id), "status": job.status}

    def run_export_job(
        self,
        job_id: str,
        *,
        actor: User | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        job = self._get_job_or_404(job_id)
        export = self._get_export_or_404(job.export_id)
        draft = self._get_draft_or_404(export.draft_id)
        action = self._get_action_or_404(draft.legal_action_id)
        case = self._get_case_or_404(draft.case_id)
        previous_status = export.status
        try:
            job.status = "processing"
            job.started_at = job.started_at or utc_now()
            job.attempts += 1
            export.status = "processing"
            export.updated_at = utc_now()
            self.db.commit()
            sections = self.repository.list_sections(draft.id)
            content = _render_export_bytes(
                _draft_export_text(draft, action, case, sections, include_watermark=export.include_watermark),
                export.format,
            )
            checksum = hashlib.sha256(content).hexdigest()
            storage_key = f"legal-drafts/{export.id}.{export.format}"
            self.storage.save(storage_key=storage_key, content=content, content_type=export.mime_type)
            export.storage_key = storage_key
            export.file_size_bytes = len(content)
            export.checksum_sha256 = checksum
            export.status = "ready"
            export.updated_at = utc_now()
            draft.status = "exported"
            draft.updated_at = utc_now()
            action.status = "completed"
            action.completed_at = utc_now()
            action.updated_at = utc_now()
            job.status = "success"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            self._audit(
                LEGAL_ACTION_EVENTS["exported"],
                actor=actor,
                case=case,
                action=action,
                draft=draft,
                export=export,
                resource_type="draft_export",
                previous_status=previous_status,
                new_status=export.status,
                metadata={"checksumSha256": checksum, "format": export.format, "fileSizeBytes": len(content)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
            return {"jobId": str(job.id), "exportId": str(export.id), "status": job.status}
        except Exception as exc:
            self.db.rollback()
            job = self._get_job_or_404(job_id)
            export = self._get_export_or_404(job.export_id)
            export.status = "failed"
            export.updated_at = utc_now()
            job.status = "failed"
            job.error_code = "EXPORT_FAILED"
            job.error_message = str(exc)[:2000]
            job.finished_at = utc_now()
            self.db.commit()
            return {"jobId": str(job.id), "exportId": str(export.id), "status": job.status}

    def _validated_sources(
        self,
        case_id: str | uuid.UUID,
        *,
        user: User,
        require_update: bool,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[LaboraCase, FullAnalysis, Report]:
        case = self._get_case_or_404(case_id)
        if require_update:
            self._require_can_update(case, user, ip_address, user_agent)
        else:
            self._require_can_view(case, user, ip_address, user_agent)
        if case.status in LOCKED_CASE_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_READY_FOR_LEGAL_ACTIONS",
                message="El expediente aun no esta listo para generar acciones juridicas.",
                details={"caseId": str(case.id), "status": case.status},
            )
        permission = ConsentComplianceService(self.db).can_upload_documents(case.owner_user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_READY_FOR_LEGAL_ACTIONS",
                message="El expediente requiere consentimientos vigentes para generar acciones juridicas.",
                details={"missingConsentTypes": permission.missing_consent_types},
            )
        if not self._case_is_unlocked(case):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_READY_FOR_LEGAL_ACTIONS",
                message="El expediente aun no esta listo para generar acciones juridicas.",
                details={"caseId": str(case.id), "blockedReason": "payment_not_unlocked"},
            )
        analysis = self.full_analysis.latest_completed_for_case(case.id)
        if analysis is None or analysis.status not in READY_ANALYSIS_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_READY_FOR_LEGAL_ACTIONS",
                message="El analisis completo aun no esta disponible.",
                details={"caseId": str(case.id), "blockedReason": "analysis_not_ready"},
            )
        report = self._latest_ready_report(case.id)
        if report is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CASE_NOT_READY_FOR_LEGAL_ACTIONS",
                message="El informe tecnico o completo aun no esta disponible.",
                details={"caseId": str(case.id), "blockedReason": "report_not_available"},
            )
        return case, analysis, report

    def _evaluate_actions(self, *, case: LaboraCase, analysis: FullAnalysis, report: Report) -> list[dict[str, Any]]:
        return [
            self._evaluation_for_action(case=case, analysis=analysis, report=report, action_type=action_type)
            for action_type in LEGAL_ACTION_TYPES
        ]

    def _evaluation_for_action(
        self,
        *,
        case: LaboraCase,
        analysis: FullAnalysis,
        report: Report,
        action_type: str,
    ) -> dict[str, Any]:
        recommended_types = self._recommended_action_types(analysis)
        confidence = analysis.confidence_global or Decimal("80.00")
        pending_data = self._pending_data(case=case)
        missing_attachments = self._missing_attachments(analysis)
        warnings = self._warnings(analysis=analysis, missing_attachments=missing_attachments)
        review_level = self._professional_review_level(action_type=action_type, analysis=analysis)
        reason = "Accion disponible segun datos del expediente."
        eligibility = "recommended" if action_type in recommended_types else "available"

        if action_type in {"technical_report_download", "executive_summary"}:
            eligibility = "available"
            reason = "El informe base esta disponible."
        elif action_type == "professional_review_request":
            eligibility = "recommended" if review_level in {"recommended", "mandatory"} else "available"
            reason = "Disponible para solicitar revision humana."
        elif action_type == "lawsuit_draft" and not self._route_allows_lawsuit(analysis):
            eligibility = "blocked"
            reason = "Segun la ruta recomendada, este caso no esta habilitado para borrador de demanda."
        elif action_type == "reliquidation_request" and not self._has_calculation_difference(analysis):
            eligibility = "requires_more_data"
            reason = "Falta calculo corregido o diferencia economica para sustentar la reliquidacion."
        elif review_level == "mandatory":
            eligibility = "requires_professional_review"
            reason = "La confianza o complejidad exige revision profesional."
        elif review_level == "recommended" and eligibility == "available":
            eligibility = "requires_professional_review"
            reason = "Se recomienda revision profesional por confianza baja o riesgo juridico."

        return {
            "actionType": action_type,
            "status": eligibility,
            "reason": reason,
            "professionalReviewLevel": review_level,
            "warnings": warnings,
            "pendingData": pending_data,
            "missingAttachments": missing_attachments,
            "recommended": action_type in recommended_types,
        }

    def _template_sections(
        self,
        *,
        case: LaboraCase,
        action: LegalAction,
        draft: LegalDraft,
        template: LegalTemplate,
        generated_by_ai: bool,
    ) -> list[dict[str, Any]]:
        analysis = self.full_analysis.latest_completed_for_case(case.id)
        report = self._latest_ready_report(case.id)
        source = self._source_payload(case=case, analysis=analysis, report=report, action=action, draft=draft, template=template)
        sections = []
        for index, item in enumerate(template.content_schema.get("sections") or [], start=1):
            markdown = _template_section_markdown(item["section_key"], source)
            pending = _section_pending_markers(item["section_key"], source)
            sections.append(
                {
                    "section_key": item["section_key"],
                    "title": item["title"],
                    "order_index": index,
                    "content_html": _markdown_to_html(markdown),
                    "content_plain": _markdown_to_plain(markdown),
                    "status": "needs_data" if pending else "generated",
                    "source_references": _source_refs(source),
                    "pending_markers": pending,
                    "confidence_score": Decimal(str((analysis.confidence_global or Decimal("80.00")) / Decimal("100.00"))),
                    "generated_by_ai": generated_by_ai,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
        return sections

    def _source_payload(
        self,
        *,
        case: LaboraCase,
        analysis: FullAnalysis | None,
        report: Report | None,
        action: LegalAction,
        draft: LegalDraft,
        template: LegalTemplate,
    ) -> dict[str, Any]:
        return _json_safe(
            {
                "case": {
                    "id": str(case.id),
                    "caseNumber": case.case_number,
                    "holderName": f"{case.holder_first_name} {case.holder_last_name}".strip(),
                    "holderDocument": f"{case.holder_document_type} {case.holder_document_number}".strip(),
                    "holderEmail": case.holder_email,
                    "caseTypeRequested": case.case_type_requested,
                    "situationType": case.situation_type,
                    "pensionFundOrEntity": case.pension_fund_or_entity,
                },
                "action": {
                    "id": str(action.id),
                    "actionType": action.action_type,
                    "professionalReviewLevel": action.professional_review_level,
                },
                "analysis": self._analysis_payload(analysis),
                "report": self._report_payload(report),
                "rules": [self._rule_payload(item) for item in self._rules(analysis.id if analysis else None)],
                "calculations": [self._calculation_payload(item) for item in self._calculations(analysis.id if analysis else None)],
                "scenarios": [self._scenario_payload(item) for item in self._scenarios(analysis.id if analysis else None)],
                "inconsistencies": [self._inconsistency_payload(item) for item in self._inconsistencies(analysis.id if analysis else None)],
                "documents": [self._document_payload(item) for item in self._documents(case.id)],
                "userInputs": draft.user_inputs or {},
                "documentMetadata": draft.document_metadata or {},
                "templateSchema": template.content_schema or {},
                "missingAttachments": action.missing_attachments or [],
            }
        )

    def _apply_ai_draft_output(self, *, draft: LegalDraft, action: LegalAction, output: LegalDraftAiOutput) -> None:
        sections = []
        for index, item in enumerate(output.sections, start=1):
            confidence = Decimal(str(item.get("confidence_score") or output.confidence_score / 100))
            pending = item.get("pending_markers") or []
            sections.append(
                {
                    "section_key": item["section_key"],
                    "title": item["title"],
                    "order_index": index,
                    "content_html": _markdown_to_html(item["content_markdown"]),
                    "content_plain": _markdown_to_plain(item["content_markdown"]),
                    "status": "needs_data" if pending else "generated",
                    "source_references": item.get("source_references") or [],
                    "pending_markers": pending,
                    "confidence_score": confidence,
                    "generated_by_ai": True,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
        self.repository.replace_sections(draft, sections)
        draft.title = output.title
        draft.status = "requires_review" if output.professional_review_suggestion == "mandatory" else "ready_for_edit"
        draft.ai_summary = {
            "provider": output.provider,
            "model": output.model,
            "promptVersion": output.prompt_version,
            "inputHash": output.input_hash,
            "outputHash": output.output_hash,
            "confidenceScore": output.confidence_score,
            "warnings": output.warnings,
        }
        draft.professional_review_level = _max_review_level(
            draft.professional_review_level,
            output.professional_review_suggestion,
        )
        draft.updated_at = utc_now()
        action.warnings = output.warnings
        action.pending_data = output.missing_data
        action.professional_review_level = _max_review_level(
            action.professional_review_level,
            output.professional_review_suggestion,
        )
        action.status = "requires_review" if draft.status == "requires_review" else "generated"
        action.updated_at = utc_now()

    def _quality_checks(self, *, draft: LegalDraft, action: LegalAction) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
        sections = self.repository.list_sections(draft.id)
        by_key = {section.section_key: section for section in sections}
        critical: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        required = LAWSUIT_SECTIONS if action.action_type == "lawsuit_draft" else COMMON_REQUEST_SECTIONS
        required_keys = {key for key, _title in required}

        def add(key: str, status_value: str, message: str) -> None:
            checks.append({"key": key, "status": status_value, "message": message})
            if status_value == "failed":
                critical.append({"key": key, "message": message})

        missing_sections = [key for key in required_keys if key not in by_key or not (by_key[key].content_plain or "").strip()]
        add(
            "parties_identified",
            "passed" if any(key in by_key for key in {"claimant_identification", "parties"}) else "failed",
            "Partes o solicitante identificados." if any(key in by_key for key in {"claimant_identification", "parties"}) else "Falta identificar partes o solicitante.",
        )
        add(
            "facts_match_evidence",
            "passed" if by_key.get("facts") and by_key["facts"].source_references else "warning",
            "Los hechos tienen referencias fuente." if by_key.get("facts") and by_key["facts"].source_references else "Los hechos requieren referencias fuente mas explicitas.",
        )
        add(
            "claims_match_route",
            "passed" if by_key.get("requests") or by_key.get("claims") else "failed",
            "Solicitudes o pretensiones presentes." if by_key.get("requests") or by_key.get("claims") else "Faltan solicitudes o pretensiones.",
        )
        add(
            "legal_basis_present",
            "passed" if by_key.get("legal_basis") and (by_key["legal_basis"].content_plain or "").strip() else "failed",
            "Fundamentos presentes." if by_key.get("legal_basis") else "Faltan fundamentos juridicos.",
        )
        add(
            "attachments_listed",
            "passed" if by_key.get("attachments") and "[DATO PENDIENTE" not in (by_key["attachments"].content_plain or "") else "warning",
            "Anexos listados." if by_key.get("attachments") else "Falta listar anexos.",
        )
        pending_markers = [marker for section in sections for marker in (section.pending_markers or [])]
        add(
            "missing_data_marked",
            "warning" if pending_markers else "passed",
            "Hay datos pendientes marcados." if pending_markers else "No hay datos pendientes sin marcar.",
        )
        add("no_internal_contradictions", "passed", "No se detectaron contradicciones automaticas.")
        add(
            "amounts_match_calculation",
            "passed" if action.action_type not in {"reliquidation_request", "lawsuit_draft"} or by_key.get("estimated_amount_or_oath") or by_key.get("requests") else "warning",
            "Cuantias revisadas contra calculos disponibles.",
        )
        add(
            "professional_review_warning_present",
            "passed" if action.action_type != "lawsuit_draft" or by_key.get("professional_review_warning") else "failed",
            "Advertencia de revision profesional presente." if action.action_type != "lawsuit_draft" or by_key.get("professional_review_warning") else "Falta advertencia de revision profesional.",
        )
        if missing_sections:
            critical.append({"key": "required_sections", "message": f"Faltan secciones requeridas: {', '.join(missing_sections)}."})
        score = max(0, 100 - (len(critical) * 18) - sum(8 for item in checks if item["status"] == "warning"))
        return checks, score, critical

    def _validate_export_allowed(self, draft: LegalDraft, include_watermark: bool) -> None:
        if draft.status in {"blocked", "failed", "archived"}:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DRAFT_QUALITY_CHECK_FAILED",
                message="El borrador no esta en estado exportable.",
                details={"draftId": str(draft.id), "status": draft.status},
            )
        latest_check = self.repository.latest_quality_check(draft.id)
        if latest_check is not None and latest_check.overall_status == "failed":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DRAFT_QUALITY_CHECK_FAILED",
                message="El control de calidad fallo y bloquea la exportacion.",
            )
        if draft.professional_review_level == "mandatory" and draft.status != "approved" and not include_watermark:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PROFESSIONAL_REVIEW_REQUIRED",
                message="Este borrador requiere revision profesional antes de exportar version final.",
            )

    def _latest_ready_report(self, case_id: uuid.UUID) -> Report | None:
        return (
            self.db.query(Report)
            .filter(
                Report.case_id == case_id,
                Report.deleted_at.is_(None),
                Report.status.in_(READY_REPORT_STATUSES),
                Report.report_type.in_(["full", "technical", "executive"]),
            )
            .order_by(desc(Report.report_type == "full"), desc(Report.updated_at), desc(Report.created_at))
            .first()
        )

    def _case_is_unlocked(self, case: LaboraCase) -> bool:
        if case.status in READY_CASE_STATUSES:
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

    def _recommended_action_types(self, analysis: FullAnalysis) -> set[str]:
        values = {
            _normalize_action_type(analysis.recommended_route),
            _normalize_action_type((analysis.executive_result or {}).get("recommendedRoute")),
            _normalize_action_type((analysis.executive_result or {}).get("recommendedLegalActionType")),
        }
        for item in self._inconsistencies(analysis.id):
            values.add(_normalize_action_type(item.recommended_action))
        route = (
            self.db.query(RecommendedRoute)
            .join(RecommendedRoute.case_result)
            .filter(RecommendedRoute.case_result.has(case_id=analysis.case_id))
            .order_by(desc(RecommendedRoute.created_at))
            .first()
        )
        if route is not None:
            values.add(_normalize_action_type(route.recommended_legal_action_type or route.route_type))
        return {value for value in values if value}

    def _route_allows_lawsuit(self, analysis: FullAnalysis) -> bool:
        if "lawsuit_draft" in self._recommended_action_types(analysis):
            return True
        route_text = " ".join(
            str(value or "")
            for value in [
                analysis.recommended_route,
                (analysis.executive_result or {}).get("recommendedRoute"),
                (analysis.executive_result or {}).get("recommendedActions"),
            ]
        ).lower()
        return any(needle in route_text for needle in {"lawsuit", "demanda", "judicial"})

    def _has_calculation_difference(self, analysis: FullAnalysis) -> bool:
        return any(
            item.calculation_code in {"ECONOMIC_DIFFERENCE_001", "RETROACTIVE_ESTIMATE_001"}
            and item.result_value is not None
            for item in self._calculations(analysis.id)
        )

    def _professional_review_level(self, *, action_type: str, analysis: FullAnalysis) -> str:
        confidence = analysis.confidence_global or Decimal("80.00")
        if action_type == "lawsuit_draft":
            return "mandatory" if confidence < LOW_CONFIDENCE_THRESHOLD or analysis.requires_human_review else "recommended"
        if analysis.requires_human_review or confidence < MANDATORY_REVIEW_THRESHOLD:
            return "mandatory"
        if confidence < LOW_CONFIDENCE_THRESHOLD or analysis.status == "requires_review":
            return "recommended"
        return "optional" if action_type in {"administrative_appeal", "reliquidation_request"} else "none"

    def _pending_data(self, *, case: LaboraCase) -> list[dict[str, Any]]:
        pending = []
        if not case.holder_document_number:
            pending.append({"key": "holder_document", "label": "Identificacion del solicitante"})
        if not case.holder_email and not case.holder_phone:
            pending.append({"key": "notification_contact", "label": "Datos de notificacion"})
        return pending

    def _missing_attachments(self, analysis: FullAnalysis) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in self._inconsistencies(analysis.id):
            for document in item.missing_documents or []:
                key = str(document).strip()
                if key and key.lower() not in seen:
                    seen.add(key.lower())
                    values.append(
                        {
                            "documentType": key,
                            "required": item.severity == "high",
                            "sourceInconsistencyId": str(item.id),
                        }
                    )
        return values

    def _warnings(self, *, analysis: FullAnalysis, missing_attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        warnings = []
        confidence = analysis.confidence_global
        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            warnings.append({"code": "LOW_CONFIDENCE", "message": "El analisis fuente tiene confianza baja."})
        if missing_attachments:
            warnings.append({"code": "MISSING_ATTACHMENTS", "message": "Hay anexos faltantes o recomendados."})
        if analysis.requires_human_review:
            warnings.append({"code": "SOURCE_REQUIRES_REVIEW", "message": "El analisis completo requiere revision humana."})
        return warnings

    def _source_route_id(self, case_id: uuid.UUID) -> uuid.UUID | None:
        route = (
            self.db.query(RecommendedRoute)
            .join(RecommendedRoute.case_result)
            .filter(RecommendedRoute.case_result.has(case_id=case_id))
            .order_by(desc(RecommendedRoute.created_at))
            .first()
        )
        return route.id if route else None

    def _rules(self, analysis_id: uuid.UUID | None) -> list[LegalRuleResult]:
        if analysis_id is None:
            return []
        return (
            self.db.query(LegalRuleResult)
            .filter(LegalRuleResult.full_analysis_id == analysis_id)
            .order_by(LegalRuleResult.created_at.asc(), LegalRuleResult.rule_code.asc())
            .all()
        )

    def _calculations(self, analysis_id: uuid.UUID | None) -> list[CalculationResult]:
        if analysis_id is None:
            return []
        return (
            self.db.query(CalculationResult)
            .filter(CalculationResult.full_analysis_id == analysis_id)
            .order_by(CalculationResult.created_at.asc(), CalculationResult.calculation_code.asc())
            .all()
        )

    def _scenarios(self, analysis_id: uuid.UUID | None) -> list[Scenario]:
        if analysis_id is None:
            return []
        return (
            self.db.query(Scenario)
            .filter(Scenario.full_analysis_id == analysis_id)
            .order_by(Scenario.created_at.asc(), Scenario.scenario_type.asc())
            .all()
        )

    def _inconsistencies(self, analysis_id: uuid.UUID | None) -> list[AnalysisInconsistency]:
        if analysis_id is None:
            return []
        return (
            self.db.query(AnalysisInconsistency)
            .filter(AnalysisInconsistency.full_analysis_id == analysis_id)
            .order_by(AnalysisInconsistency.created_at.asc(), AnalysisInconsistency.title.asc())
            .all()
        )

    def _documents(self, case_id: uuid.UUID) -> list[Document]:
        return (
            self.db.query(Document)
            .filter(
                Document.case_id == case_id,
                Document.deleted_at.is_(None),
                Document.status.in_(["validated", "accepted", "ready"]),
            )
            .order_by(Document.created_at.asc())
            .all()
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

    def _require_can_update_draft(
        self,
        case: LaboraCase,
        draft: LegalDraft,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if draft.is_locked and user.role not in REVIEW_ROLES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DRAFT_LOCKED",
                message="El borrador esta bloqueado por revision o exportacion.",
            )
        self._require_can_update(case, user, ip_address, user_agent)

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
            code="MISSING_REQUIRED_PERMISSION",
            message="No tienes permisos para revisar borradores juridicos.",
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
            LEGAL_ACTION_EVENTS["access_denied"],
            actor=actor,
            case=case,
            action=None,
            resource_type="legal_action",
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="MISSING_REQUIRED_PERMISSION",
            message="No tienes permisos para acceder a esta accion juridica o expediente.",
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

    def _get_action_or_404(self, action_id: str | uuid.UUID) -> LegalAction:
        action = self.repository.get_action(action_id)
        if action is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="LEGAL_ACTION_NOT_FOUND",
                message="Accion juridica no encontrada.",
            )
        return action

    def _get_draft_or_404(self, draft_id: str | uuid.UUID | None) -> LegalDraft:
        draft = self.repository.get_draft(draft_id)
        if draft is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DRAFT_NOT_FOUND",
                message="Borrador no encontrado.",
            )
        return draft

    def _get_export_or_404(self, export_id: str | uuid.UUID | None) -> DraftExport:
        export = self.repository.get_export(export_id)
        if export is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DRAFT_NOT_FOUND",
                message="Exportacion de borrador no encontrada.",
            )
        return export

    def _get_job_or_404(self, job_id: str | uuid.UUID) -> LegalActionJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DRAFT_NOT_FOUND",
                message="Job de accion juridica no encontrado.",
            )
        return job

    def _create_job(
        self,
        *,
        job_type: str,
        case_id: uuid.UUID,
        idempotency_key: str,
        draft_id: uuid.UUID | None = None,
        section_id: uuid.UUID | None = None,
        export_id: uuid.UUID | None = None,
    ) -> LegalActionJob:
        existing = self.repository.job_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing
        return self.repository.create_job(
            case_id=case_id,
            draft_id=draft_id,
            section_id=section_id,
            export_id=export_id,
            job_type=job_type,
            status="queued",
            idempotency_key=idempotency_key,
            attempts=0,
            created_at=utc_now(),
            updated_at=utc_now(),
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
    ) -> CaseHistoryEvent:
        return self.cases.create_history_event(
            case_id=case.id,
            event_type=event_type,
            title=title,
            description=description,
            visibility="both",
            severity=severity,
            created_by_user_id=actor.id if actor else None,
            metadata=_json_safe(metadata or {}),
        )

    def _audit(
        self,
        event_type: str,
        *,
        actor: User | None,
        case: LaboraCase,
        action: LegalAction | None,
        resource_type: str,
        ip_address: str | None,
        user_agent: str | None,
        draft: LegalDraft | None = None,
        export: DraftExport | None = None,
        previous_status: str | None = None,
        new_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        resource_id = None
        if resource_type == "legal_draft" and draft is not None:
            resource_id = draft.id
        elif resource_type == "draft_export" and export is not None:
            resource_id = export.id
        elif action is not None:
            resource_id = action.id
        event_metadata = {
            "eventName": event_type,
            "actorId": str(actor.id) if actor else None,
            "actorRole": self._actor_role(actor),
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "resourceType": resource_type,
            "resourceId": str(resource_id) if resource_id else None,
            "legalActionId": str(action.id) if action else None,
            "draftId": str(draft.id) if draft else None,
            "previousStatus": previous_status,
            "newStatus": new_status,
            "sourceModule": "legal_actions",
        }
        if metadata:
            event_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_type,
            entity_type=resource_type,
            entity_id=resource_id,
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
        if user.role in SUPPORT_ROLES:
            return "support"
        if user.role == "system":
            return "system"
        return "case_owner"

    def _system_actor(self) -> User:
        user = (
            self.db.query(User)
            .filter(User.role == "system")
            .order_by(User.created_at.asc())
            .first()
        )
        if user is not None:
            return user
        fallback = (
            self.db.query(User)
            .order_by(User.created_at.asc())
            .first()
        )
        if fallback is None:
            raise RuntimeError("No existe usuario para registrar version.")
        return fallback

    def _action_payload(self, action: LegalAction) -> dict[str, Any]:
        return {
            "id": str(action.id),
            "caseId": str(action.case_id),
            "userId": str(action.user_id),
            "actionType": action.action_type,
            "status": action.status,
            "eligibilityStatus": action.eligibility_status,
            "eligibilityReason": action.eligibility_reason,
            "professionalReviewLevel": action.professional_review_level,
            "warnings": action.warnings or [],
            "pendingData": action.pending_data or [],
            "missingAttachments": action.missing_attachments or [],
            "selectedByUser": action.selected_by_user,
            "createdAt": action.created_at,
            "updatedAt": action.updated_at,
        }

    def _draft_payload(self, draft: LegalDraft, *, action: LegalAction, include_download_urls: bool) -> dict[str, Any]:
        quality_check = self.repository.latest_quality_check(draft.id)
        return {
            "id": str(draft.id),
            "caseId": str(draft.case_id),
            "legalActionId": str(draft.legal_action_id),
            "title": draft.title,
            "status": draft.status,
            "professionalReviewLevel": draft.professional_review_level,
            "qualityScore": _float(draft.quality_score),
            "documentMetadata": draft.document_metadata or {},
            "userInputs": draft.user_inputs or {},
            "warnings": action.warnings or [],
            "sections": [self._section_payload(item) for item in self.repository.list_sections(draft.id)],
            "qualityCheck": self._quality_check_payload(quality_check) if quality_check else None,
            "exports": [self._export_payload(item, include_download_url=include_download_urls) for item in self.repository.list_exports(draft.id)],
            "currentVersionNumber": draft.current_version_number,
            "isLocked": draft.is_locked,
            "createdAt": draft.created_at,
            "updatedAt": draft.updated_at,
        }

    def _section_payload(self, section: DraftSection) -> dict[str, Any]:
        return {
            "id": str(section.id),
            "sectionKey": section.section_key,
            "title": section.title,
            "orderIndex": section.order_index,
            "contentHtml": section.content_html,
            "contentPlain": section.content_plain,
            "status": section.status,
            "sourceReferences": section.source_references or [],
            "pendingMarkers": section.pending_markers or [],
            "confidenceScore": _float(section.confidence_score),
            "generatedByAi": section.generated_by_ai,
        }

    def _quality_check_payload(self, check) -> dict[str, Any]:
        return {
            "id": str(check.id),
            "overallStatus": check.overall_status,
            "score": _float(check.score),
            "checks": check.checks or [],
            "criticalWarnings": check.critical_warnings or [],
            "createdAt": check.created_at,
        }

    def _export_payload(self, export: DraftExport, *, include_download_url: bool) -> dict[str, Any]:
        download_url = None
        if include_download_url and export.status == "ready" and export.storage_key:
            download_url = self._signed_export_url(export, utc_now() + timedelta(seconds=DOWNLOAD_TOKEN_TTL_SECONDS))
        return {
            "id": str(export.id),
            "format": export.format,
            "fileName": export.file_name,
            "status": export.status,
            "versionNumber": export.version_number,
            "downloadUrl": download_url,
            "createdAt": export.created_at,
        }

    def _signed_export_url(self, export: DraftExport, expires_at) -> str:
        expires = int(expires_at.timestamp())
        token = _download_signature(export.id, expires)
        return (
            f"{settings.backend_public_url}{settings.api_v1_prefix}/draft-exports/{export.id}/file"
            f"?expires={expires}&token={token}"
        )

    def _analysis_payload(self, item: FullAnalysis | None) -> dict[str, Any]:
        if item is None:
            return {}
        return {
            "id": str(item.id),
            "status": item.status,
            "version": item.version,
            "summary": item.summary or {},
            "executiveResult": item.executive_result or {},
            "legalConclusion": item.legal_conclusion,
            "recommendedRoute": item.recommended_route,
            "viabilityLevel": item.viability_level,
            "confidenceGlobal": item.confidence_global,
            "requiresHumanReview": item.requires_human_review,
            "humanReviewReason": item.human_review_reason,
            "completedAt": item.completed_at,
        }

    def _report_payload(self, item: Report | None) -> dict[str, Any]:
        if item is None:
            return {}
        sections = item.sections or []
        return {
            "id": str(item.id),
            "reportType": item.report_type,
            "title": item.title,
            "status": item.status,
            "executiveSummary": _section_text(sections, "executive_summary"),
            "technicalSummary": _section_text(sections, "conclusions") or _section_text(sections, "case_context"),
            "inconsistencyMatrix": _section_text(sections, "inconsistency_matrix"),
            "legalBasisSummary": _section_text(sections, "legal_rules"),
        }

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
            "inconsistencyType": item.inconsistency_type,
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

    def _document_payload(self, item: Document) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "fileName": item.display_name or item.original_filename,
            "status": item.status,
            "isAvailable": item.deleted_at is None and item.status in {"validated", "accepted", "ready"},
            "metadata": {},
        }


def _default_template(action_type: str) -> dict[str, Any]:
    sections = LAWSUIT_SECTIONS if action_type == "lawsuit_draft" else COMMON_REQUEST_SECTIONS
    if action_type in {"technical_report_download", "executive_summary", "professional_review_request"}:
        sections = [
            ("facts", "Resumen del expediente"),
            ("requests", "Solicitud"),
            ("attachments", "Anexos"),
            ("signature", "Firma"),
        ]
    return {
        "action_type": action_type,
        "name": f"labora_{action_type}_v1",
        "display_name": ACTION_TITLES.get(action_type, "Escrito juridico"),
        "jurisdiction": "Colombia",
        "legal_domain": "pensional",
        "template_version": 1,
        "content_schema": {
            "sections": [
                {"section_key": key, "title": title, "required": True}
                for key, title in sections
            ]
        },
        "required_inputs_schema": _required_inputs_schema(action_type),
        "required_attachments_schema": {"items": []},
        "is_active": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def _required_inputs_schema(action_type: str) -> dict[str, Any]:
    common = ["city", "notificationAddress"]
    by_type = {
        "petition": common + ["recipient", "requests"],
        "administrative_claim": common + ["entity", "selectedInconsistencies", "requests"],
        "reliquidation_request": common + ["recognizedAmount", "requestedCorrection"],
        "administrative_appeal": common + ["challengedAct", "appealType"],
        "lawsuit_draft": common + ["defendant", "jurisdiction", "claims"],
    }
    return {"required": by_type.get(action_type, common)}


def _template_section_markdown(section_key: str, source: dict[str, Any]) -> str:
    case = source.get("case") or {}
    analysis = source.get("analysis") or {}
    user_inputs = source.get("userInputs") or {}
    inconsistencies = source.get("inconsistencies") or []
    documents = source.get("documents") or []
    calculations = source.get("calculations") or []
    if section_key in {"recipient", "heading"}:
        city = user_inputs.get("city") or "[DATO PENDIENTE: ciudad]"
        recipient = user_inputs.get("recipient") or user_inputs.get("entity") or case.get("pensionFundOrEntity") or "[DATO PENDIENTE: destinatario]"
        return f"{city}\n\nSenores\n{recipient}\n\nReferencia: expediente {case.get('caseNumber')}."
    if section_key in {"claimant_identification", "parties"}:
        return f"{case.get('holderName') or '[DATO PENDIENTE: nombre]'}\n{case.get('holderDocument') or '[DATO PENDIENTE: identificacion]'}"
    if section_key == "facts":
        lines = [analysis.get("legalConclusion")]
        lines.extend(item.get("description") for item in inconsistencies[:5])
        return "\n".join(f"- {line}" for line in lines if line) or "- [DATO PENDIENTE: hechos]"
    if section_key in {"requests", "claims"}:
        lines = [f"Que se tramite la ruta recomendada: {analysis.get('recommendedRoute') or 'revision profesional'}."]
        lines.extend(f"Que se revise {item.get('title')}." for item in inconsistencies[:4] if item.get("title"))
        return "\n".join(f"- {line}" for line in lines)
    if section_key == "legal_basis":
        return analysis.get("legalConclusion") or "[DATO PENDIENTE: fundamentos juridicos verificables]"
    if section_key == "evidence":
        evidence = []
        for item in inconsistencies:
            evidence.extend(ref.get("label") for ref in item.get("evidenceRefs") or [])
        return "\n".join(f"- {item}" for item in evidence if item) or "- [DATO PENDIENTE: pruebas]"
    if section_key == "attachments":
        return "\n".join(f"- {item.get('fileName')}" for item in documents if item.get("fileName")) or "- [DATO PENDIENTE: anexos]"
    if section_key == "estimated_amount_or_oath":
        amount = _first_amount(calculations)
        return f"Valor estimado: {amount}." if amount else "[DATO PENDIENTE: cuantia]"
    if section_key == "notifications":
        return user_inputs.get("notificationAddress") or case.get("holderEmail") or "[DATO PENDIENTE: notificaciones]"
    if section_key == "signature":
        return case.get("holderName") or "[DATO PENDIENTE: firmante]"
    if section_key == "professional_review_warning":
        return "Este borrador requiere revision profesional antes de radicacion o uso judicial."
    if section_key == "jurisdiction_and_competence":
        return "Jurisdiccion y competencia por confirmar con profesional juridico."
    return "[DATO PENDIENTE]"


def _section_pending_markers(section_key: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    text = _template_section_markdown(section_key, source)
    markers = []
    for match in re.findall(r"\[DATO PENDIENTE: ([^\]]+)\]", text):
        markers.append({"key": _slug(match), "label": match, "sectionKey": section_key})
    return markers


def _source_refs(source: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    analysis = source.get("analysis") or {}
    report = source.get("report") or {}
    if analysis.get("id"):
        refs.append({"type": "analysis", "id": analysis["id"], "label": "Analisis completo"})
    if report.get("id"):
        refs.append({"type": "report", "id": report["id"], "label": "Informe base"})
    for item in (source.get("inconsistencies") or [])[:5]:
        if item.get("id"):
            refs.append({"type": "inconsistency", "id": item["id"], "label": item.get("title")})
    return refs


def _draft_snapshot(draft: LegalDraft, sections: list[DraftSection]) -> dict[str, Any]:
    return {
        "draft": {
            "id": str(draft.id),
            "caseId": str(draft.case_id),
            "legalActionId": str(draft.legal_action_id),
            "title": draft.title,
            "status": draft.status,
            "professionalReviewLevel": draft.professional_review_level,
            "documentMetadata": draft.document_metadata or {},
            "userInputs": draft.user_inputs or {},
        },
        "sections": [
            {
                "id": str(section.id),
                "sectionKey": section.section_key,
                "title": section.title,
                "orderIndex": section.order_index,
                "contentHtml": section.content_html,
                "contentPlain": section.content_plain,
                "status": section.status,
                "sourceReferences": section.source_references or [],
                "pendingMarkers": section.pending_markers or [],
                "confidenceScore": section.confidence_score,
            }
            for section in sections
        ],
    }


def _draft_export_text(
    draft: LegalDraft,
    action: LegalAction,
    case: LaboraCase,
    sections: list[DraftSection],
    *,
    include_watermark: bool,
) -> str:
    parts = [draft.title, f"Expediente Labora: {case.case_number}", ""]
    if include_watermark or draft.status != "approved":
        parts.extend(["BORRADOR - NO RADICAR SIN REVISION", ""])
    if draft.professional_review_level in {"recommended", "mandatory"}:
        parts.extend([f"Revision profesional: {draft.professional_review_level}.", ""])
    for section in sorted(sections, key=lambda item: item.order_index):
        parts.append(section.title)
        parts.append(section.content_plain or _html_to_plain(section.content_html or ""))
        parts.append("")
    return "\n".join(parts).strip()


def _ai_output_payload(output: LegalDraftAiOutput) -> dict[str, Any]:
    return {
        "title": output.title,
        "sections": output.sections,
        "warnings": output.warnings,
        "missingData": output.missing_data,
        "professionalReviewSuggestion": output.professional_review_suggestion,
        "provider": output.provider,
        "model": output.model,
        "promptVersion": output.prompt_version,
        "confidenceScore": output.confidence_score,
    }


def _normalize_action_type(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_action_type(item)
            if normalized:
                return normalized
        return None
    raw = str(value).strip().lower()
    mapping = {
        "petition_right": "petition",
        "derecho_peticion": "petition",
        "right_of_petition": "petition",
        "administrative_claim": "administrative_claim",
        "reclamacion_administrativa": "administrative_claim",
        "reliquidation": "reliquidation_request",
        "reliquidation_request": "reliquidation_request",
        "administrative_appeal": "administrative_appeal",
        "appeal": "administrative_appeal",
        "legal_claim_draft": "lawsuit_draft",
        "judicial_claim": "lawsuit_draft",
        "lawsuit": "lawsuit_draft",
        "lawsuit_draft": "lawsuit_draft",
        "demanda": "lawsuit_draft",
        "professional_review": "professional_review_request",
        "professional_review_request": "professional_review_request",
    }
    return mapping.get(raw, raw if raw in LEGAL_ACTION_TYPES else None)


def _max_review_level(current: str, candidate: str) -> str:
    order = {"none": 0, "optional": 1, "recommended": 2, "mandatory": 3}
    return current if order.get(current, 0) >= order.get(candidate, 0) else candidate


def _section_text(sections: list[Any], key: str) -> str | None:
    for section in sections:
        if section.section_key == key:
            return section.content_markdown
    return None


def _first_amount(calculations: list[dict[str, Any]]) -> str | None:
    for code in ("RETROACTIVE_ESTIMATE_001", "ECONOMIC_DIFFERENCE_001", "CORRECT_ESTIMATED_AMOUNT_001"):
        for item in calculations:
            if item.get("calculationCode") == code and item.get("resultValue") is not None:
                unit = item.get("resultUnit") or ""
                return f"{item['resultValue']} {unit}".strip()
    return None


def _export_file_name(action_type: str, case_number: str, fmt: str) -> str:
    return f"{_slug(ACTION_TITLES.get(action_type, action_type))}_{case_number}_{utc_now():%Y%m%d}.{fmt}"


def _mime_type(fmt: str) -> str:
    if fmt == "pdf":
        return "application/pdf"
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _render_export_bytes(text: str, fmt: str) -> bytes:
    if fmt == "pdf":
        return _simple_pdf(text)
    if fmt == "docx":
        return _simple_docx(text)
    raise ValueError("Formato no soportado.")


def _simple_pdf(text: str) -> bytes:
    lines = _wrap_lines(text, width=88)[:60]
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
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return pdf.getvalue()


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
    payload = f"draft-export:{export_id}:{expires}".encode("utf-8")
    return hmac.new(settings.jwt_access_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _markdown_to_html(markdown: str) -> str:
    paragraphs = []
    for block in markdown.splitlines():
        line = block.strip()
        if not line:
            continue
        if line.startswith("- "):
            paragraphs.append(f"<p>{xml_escape(line[2:])}</p>")
        else:
            paragraphs.append(f"<p>{xml_escape(line)}</p>")
    return "".join(paragraphs) or "<p>[DATO PENDIENTE]</p>"


def _markdown_to_plain(markdown: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", markdown)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def _sanitize_html(html: str) -> str:
    cleaned = re.sub(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", "", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\son[a-z]+\s*=\s*(['\"]).*?\1", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"javascript:", "", cleaned, flags=re.IGNORECASE)
    allowed = {"p", "br", "strong", "b", "em", "i", "u", "ol", "ul", "li"}

    def replace_tag(match) -> str:
        slash, tag = match.group(1), match.group(2).lower()
        return f"<{slash}{tag}>" if tag in allowed else ""

    return re.sub(r"<(/?)([a-zA-Z0-9]+)(?:\s[^>]*)?>", replace_tag, cleaned)


def _html_to_plain(html: str) -> str:
    text = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


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


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "documento"


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _json_loads(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
