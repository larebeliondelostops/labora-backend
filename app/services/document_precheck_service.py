import logging
import uuid
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.case import LaboraCase
from app.models.document import Document
from app.models.document_precheck import DocumentPrecheck, OcrJob
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.document_precheck_repository import DocumentPrecheckRepository
from app.repositories.document_repository import DocumentRepository
from app.schemas.document_precheck import ManualReviewRequest
from app.services.ai_provider import (
    AiInvalidResponseError,
    AiProviderError,
    AiProviderResult,
    ai_provider_factory,
    ALLOWED_DOCUMENT_TYPES,
)
from app.services.consent_service import ConsentComplianceService
from app.services.document_issue_service import build_issue
from app.services.document_storage_service import DocumentStorageService
from app.services.ocr_preview_service import OcrPreviewError, OcrPreviewService, StorageReadError
from app.utils.dates import utc_now


logger = logging.getLogger(__name__)

ADMIN_REVIEW_ROLES = {"admin", "legal_admin", "reviewer", "legal_ops", "legal_reviewer"}
ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
ACTIVE_PRECHECK_STATUSES = {"queued", "in_progress"}
COMPLETE_DOCUMENT_STATUSES = {"uploaded", "processing", "validated", "requires_review", "rejected"}
AI_PAGE_TEXT_PREVIEW_CHARS = 900
AI_TOTAL_TEXT_PREVIEW_CHARS = 4500


class DocumentPrecheckService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.documents = DocumentRepository(db)
        self.prechecks = DocumentPrecheckRepository(db)
        self.storage = DocumentStorageService()
        self.audit = AuditEventRepository(db)

    def start_precheck(
        self,
        case_id: str,
        *,
        document_id: str,
        force: bool,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case, document = self._document_for_case_or_error(
            case_id=case_id,
            document_id=document_id,
        )
        self._require_can_update_case(case, user)
        self._require_sensitive_consent(user)
        self._require_document_available(document)

        existing = self.prechecks.latest_for_document(document.id)
        if existing is not None and not force:
            if existing.status in ACTIVE_PRECHECK_STATUSES or existing.status in {
                "completed",
                "blocked",
                "requires_review",
                "error",
            }:
                self._audit(
                    "ia_documental_preliminar.viewed",
                    precheck=existing,
                    document=document,
                    actor=user,
                    metadata={"reused": True},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                self.db.commit()
                return self._precheck_detail(existing, document=document)

        if force and existing is not None:
            self.prechecks.unset_latest_for_document(document.id)

        now = utc_now()
        precheck = self.prechecks.create_precheck(
            case_id=case.id,
            document_id=document.id,
            status="queued",
            traffic_light="gray",
            is_latest=True,
            created_by=user.id,
            created_at=now,
            updated_at=now,
        )
        self._audit(
            "ia_documental_preliminar.created",
            precheck=precheck,
            document=document,
            actor=user,
            new_state=_precheck_state(precheck),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            "ia_documental_preliminar.queued",
            precheck=precheck,
            document=document,
            actor=user,
            new_state=_precheck_state(precheck),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self._process_precheck(
            precheck,
            document=document,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(precheck)
        return self._precheck_detail(precheck, document=document)

    def list_prechecks(
        self,
        case_id: str,
        *,
        document_id: str | None,
        latest: bool,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view_case(case, user)
        items = self.prechecks.list_for_case(
            case.id,
            document_id=document_id,
            latest=latest,
        )
        for item in items:
            document = self.documents.get(item.document_id)
            if document is not None:
                self._audit(
                    "ia_documental_preliminar.viewed",
                    precheck=item,
                    document=document,
                    actor=user,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "items": [
                self._precheck_detail(
                    item,
                    document=self.documents.get(item.document_id),
                    include_pages=document_id is not None,
                )
                for item in items
            ],
        }

    def get_precheck(
        self,
        case_id: str,
        precheck_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view_case(case, user)
        precheck = self._get_precheck_or_404(precheck_id)
        if precheck.case_id != case.id:
            raise self._precheck_not_found()
        document = self.documents.get(precheck.document_id)
        self._audit(
            "ia_documental_preliminar.viewed",
            precheck=precheck,
            document=document,
            actor=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._precheck_detail(precheck, document=document, include_pages=True)

    def create_ocr_preview(
        self,
        document_id: str,
        *,
        max_pages: int,
        include_text_preview: bool,
        force: bool,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_view_case(case, user)
        self._require_sensitive_consent(user)
        self._require_document_available(document)
        try:
            job = OcrPreviewService(self.db).create_preview(
                document,
                actor=user,
                max_pages=max_pages,
                include_text_preview=include_text_preview,
                force=force,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.db.commit()
        except OcrPreviewError as exc:
            self.db.commit()
            raise ApiError(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code=exc.code,
                message=str(exc),
            ) from exc
        return {
            "ocrJobId": str(job.id),
            "documentId": str(document.id),
            "status": job.status,
        }

    def get_ocr_preview(
        self,
        document_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        document = self._get_document_or_404(document_id)
        case = self._get_case_or_404(document.case_id)
        self._require_can_view_case(case, user)
        self._require_sensitive_consent(user)
        self._require_document_available(document)
        job = self.prechecks.latest_ocr_job(document.id)
        if job is None:
            try:
                job = OcrPreviewService(self.db).create_preview(
                    document,
                    actor=user,
                    max_pages=5,
                    include_text_preview=True,
                    force=False,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            except OcrPreviewError as exc:
                self.db.commit()
                raise ApiError(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    code=exc.code,
                    message=str(exc),
                ) from exc
        self._audit(
            "ia_documental_preliminar.viewed",
            precheck=None,
            document=document,
            actor=user,
            metadata={"ocrJobId": str(job.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "documentId": str(document.id),
            **self._ocr_summary(job, include_pages=True),
        }

    def manual_review(
        self,
        precheck_id: str,
        payload: ManualReviewRequest,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if user.role not in ADMIN_REVIEW_ROLES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="UNAUTHORIZED",
                message="No tienes permisos para revisar prechequeos documentales.",
            )
        precheck = self._get_precheck_or_404(precheck_id)
        document = self.documents.get(precheck.document_id)
        previous_state = _precheck_state(precheck)
        precheck.manual_decision = payload.decision
        precheck.manual_traffic_light = payload.traffic_light
        precheck.manual_notes = payload.notes
        precheck.manually_reviewed_by = user.id
        precheck.manually_reviewed_at = utc_now()
        precheck.decision = payload.decision
        precheck.traffic_light = payload.traffic_light
        precheck.status = "completed" if payload.traffic_light in {"green", "yellow"} else "requires_review"
        precheck.updated_at = utc_now()
        self._audit(
            "ia_documental_preliminar.manual_reviewed",
            precheck=precheck,
            document=document,
            actor=user,
            previous_state=previous_state,
            new_state=_precheck_state(precheck),
            metadata={
                "notes": bool(payload.notes),
                "issuesToResolve": payload.issues_to_resolve,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        self.db.refresh(precheck)
        return self._precheck_detail(precheck, document=document, include_pages=True)

    def _process_precheck(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        request_id = uuid.uuid4().hex
        previous_state = _precheck_state(precheck)
        precheck.status = "in_progress"
        precheck.started_at = utc_now()
        precheck.updated_at = precheck.started_at
        logger.info(
            "Starting document precheck",
            extra={
                "case_id": str(precheck.case_id),
                "document_id": str(document.id),
                "precheck_id": str(precheck.id),
                "request_id": request_id,
                "storage_key": document.storage_key,
                "document_size_bytes": document.size_bytes,
            },
        )
        self._audit(
            "ia_documental_preliminar.started",
            precheck=precheck,
            document=document,
            actor=actor,
            previous_state=previous_state,
            new_state=_precheck_state(precheck),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        try:
            ocr_job = OcrPreviewService(self.db).create_preview(
                document,
                actor=actor,
                max_pages=5,
                include_text_preview=True,
                force=True,
                precheck_id=precheck.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            ocr_issues = _issues_from_ocr_job(ocr_job)
            critical_ocr = any(issue["severity"] == "critical" for issue in ocr_issues)
            if critical_ocr:
                logger.warning(
                    "Document precheck blocked by OCR quality",
                    extra={
                        "case_id": str(precheck.case_id),
                        "document_id": str(document.id),
                        "precheck_id": str(precheck.id),
                        "request_id": request_id,
                        "storage_key": document.storage_key,
                        "ocr_job_id": str(ocr_job.id),
                        "ocr_method": ocr_job.engine,
                        "pages_processed": ocr_job.pages_processed,
                        "characters_extracted": _ocr_characters(ocr_job),
                        "issue_codes": [issue["code"] for issue in ocr_issues],
                    },
                )
                self._finish_without_ai(
                    precheck,
                    document=document,
                    issues=ocr_issues,
                    status_value="blocked",
                    decision="requires_reupload",
                    traffic_light="red",
                    summary="El documento no es legible para preanalisis documental.",
                    confidence_score=Decimal("0.0000"),
                    actor=actor,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                )
                return

            provider = ai_provider_factory()
            provider_name = getattr(provider, "provider_name", "unknown")
            provider_model = getattr(provider, "model", "unknown")
            self._audit(
                "ia_documental_preliminar.ai_classification_started",
                precheck=precheck,
                document=document,
                actor=actor,
                metadata={"provider": provider_name, "model": provider_model},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            logger.info(
                "Starting AI document classification",
                extra={
                    "case_id": str(precheck.case_id),
                    "document_id": str(document.id),
                    "precheck_id": str(precheck.id),
                    "request_id": request_id,
                    "storage_key": document.storage_key,
                    "ocr_method": ocr_job.engine,
                    "pages_processed": ocr_job.pages_processed,
                    "characters_extracted": _ocr_characters(ocr_job),
                    "ai_provider": provider_name,
                    "ai_model": provider_model,
                },
            )
            classification_payload = _classification_input(
                document=document,
                ocr_job=ocr_job,
                precheck_id=precheck.id,
                request_id=request_id,
            )
            ai_result = provider.classify_document(classification_payload)
            self._persist_ai_result(
                precheck,
                document=document,
                ocr_job=ocr_job,
                ocr_issues=ocr_issues,
                ai_result=ai_result,
                actor=actor,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        except StorageReadError as exc:
            self._finish_technical_failure(
                precheck,
                document=document,
                code=exc.code,
                issue_code=exc.issue_code,
                message=str(exc),
                actor=actor,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )
        except OcrPreviewError as exc:
            self._finish_technical_failure(
                precheck,
                document=document,
                code=exc.code,
                issue_code=exc.issue_code,
                message=str(exc),
                actor=actor,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )
        except AiInvalidResponseError as exc:
            self._finish_provider_failure(
                precheck,
                document=document,
                code="AI_PROVIDER_INVALID_RESPONSE",
                issue_code="provider_invalid_json",
                message="La respuesta del proveedor IA no pudo validarse.",
                details=getattr(exc, "details", None),
                actor=actor,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )
        except AiProviderError as exc:
            issue_code = {
                "AI_PROVIDER_NOT_CONFIGURED": "ai_provider_not_configured",
                "AI_PROVIDER_CONFIGURATION_ERROR": "ai_provider_not_configured",
                "AI_PROVIDER_AUTH_ERROR": "ai_provider_auth_error",
                "AI_PROVIDER_BAD_REQUEST": "ai_provider_bad_request",
                "AI_PROVIDER_BILLING_ERROR": "ai_provider_billing_error",
                "AI_PROVIDER_MODEL_NOT_FOUND": "ai_provider_model_not_found",
                "AI_PROVIDER_JSON_SCHEMA_ERROR": "ai_provider_json_schema_error",
                "AI_PROVIDER_TOKEN_LIMIT": "ai_provider_token_limit",
                "AI_PROVIDER_TIMEOUT": "provider_timeout",
                "AI_PROVIDER_RATE_LIMITED": "provider_rate_limited",
                "AI_PROVIDER_INVALID_RESPONSE": "provider_invalid_json",
            }.get(exc.code, "ai_provider_error")
            self._finish_provider_failure(
                precheck,
                document=document,
                code=exc.code,
                issue_code=issue_code,
                message=str(exc) or "No fue posible completar la clasificacion IA.",
                details=getattr(exc, "details", None),
                actor=actor,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )

    def _finish_without_ai(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document,
        issues: list[dict[str, Any]],
        status_value: str,
        decision: str,
        traffic_light: str,
        summary: str,
        confidence_score: Decimal,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
        request_id: str | None = None,
    ) -> None:
        previous_state = _precheck_state(precheck)
        precheck.status = status_value
        precheck.decision = decision
        precheck.traffic_light = traffic_light
        precheck.confidence_score = confidence_score
        precheck.summary = summary
        precheck.result_json = {"issues": issues, "summary": summary}
        precheck.completed_at = utc_now() if status_value != "error" else None
        precheck.failed_at = utc_now() if status_value == "error" else None
        precheck.error_code = error_code
        precheck.error_message = error_message
        precheck.updated_at = utc_now()
        self.prechecks.replace_issues(precheck.id, document.id, issues)
        logger.info(
            "Document precheck finished without AI result",
            extra={
                "case_id": str(precheck.case_id),
                "document_id": str(document.id),
                "precheck_id": str(precheck.id),
                "request_id": request_id,
                "storage_key": document.storage_key,
                "status": precheck.status,
                "decision": precheck.decision,
                "traffic_light": precheck.traffic_light,
                "confidence_score": _float(precheck.confidence_score),
                "error_code": error_code,
                "issue_codes": [issue["code"] for issue in issues],
            },
        )
        self._audit_terminal(precheck, document=document, actor=actor, previous_state=previous_state, ip_address=ip_address, user_agent=user_agent)

    def _persist_ai_result(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document,
        ocr_job: OcrJob,
        ocr_issues: list[dict[str, Any]],
        ai_result: AiProviderResult,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        previous_state = _precheck_state(precheck)
        output = ai_result.output
        ai_issues = [
            {
                "code": issue.code,
                "severity": issue.severity,
                "page_number": issue.page_number,
                "title": issue.title,
                "message": issue.message,
                "suggested_action": issue.suggested_action,
                "metadata": {"source": "ai"},
            }
            for issue in output.issues
        ]
        issues = [*ocr_issues, *ai_issues]
        if not output.is_labor_or_pension_related:
            issues.append(build_issue("not_labor_or_pension_document", severity="critical"))
        if output.confidence_score < 0.65:
            issues.append(build_issue("low_confidence"))

        decision = _decision_from_result(
            traffic_light=output.traffic_light,
            confidence_score=output.confidence_score,
            is_suitable=output.is_suitable_for_preanalysis,
            issues=issues,
        )
        precheck.status = "completed" if decision in {"suitable", "suitable_with_observations"} else "requires_review" if decision == "requires_human_review" else "blocked"
        precheck.decision = decision
        precheck.traffic_light = _traffic_light(output.traffic_light, issues, output.confidence_score)
        precheck.confidence_score = Decimal(str(round(output.confidence_score, 4)))
        precheck.provider = ai_result.provider
        precheck.model = ai_result.model
        precheck.input_hash = ai_result.input_hash
        precheck.summary = output.summary
        precheck.result_json = output.model_dump(by_alias=True)
        precheck.completed_at = utc_now()
        precheck.updated_at = precheck.completed_at
        self.prechecks.replace_issues(precheck.id, document.id, issues)
        self.prechecks.create_ai_confidence(
            document_precheck_id=precheck.id,
            provider=ai_result.provider,
            model=ai_result.model,
            task=ai_result.task,
            score=Decimal(str(round(output.confidence_score, 4))),
            rationale=output.summary[:1000],
            raw_response_hash=ai_result.raw_response_hash,
            tokens_input=ai_result.tokens_input,
            tokens_output=ai_result.tokens_output,
            latency_ms=ai_result.latency_ms,
            created_at=utc_now(),
        )
        logger.info(
            "AI document classification completed",
            extra={
                "case_id": str(precheck.case_id),
                "document_id": str(document.id),
                "precheck_id": str(precheck.id),
                "storage_key": document.storage_key,
                "ocr_job_id": str(ocr_job.id),
                "ocr_method": ocr_job.engine,
                "pages_processed": ocr_job.pages_processed,
                "characters_extracted": _ocr_characters(ocr_job),
                "ai_provider": ai_result.provider,
                "ai_model": ai_result.model,
                "confidence_score": output.confidence_score,
                "decision": precheck.decision,
                "traffic_light": precheck.traffic_light,
                "status": precheck.status,
            },
        )
        self._audit(
            "ia_documental_preliminar.ai_classification_completed",
            precheck=precheck,
            document=document,
            actor=actor,
            metadata={
                "provider": ai_result.provider,
                "model": ai_result.model,
                "confidenceScore": output.confidence_score,
                "ocrJobId": str(ocr_job.id),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit_terminal(precheck, document=document, actor=actor, previous_state=previous_state, ip_address=ip_address, user_agent=user_agent)

    def _finish_technical_failure(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document,
        code: str,
        issue_code: str,
        message: str,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None = None,
    ) -> None:
        logger.exception(
            "Document precheck technical failure",
            extra={
                "case_id": str(precheck.case_id),
                "document_id": str(document.id),
                "precheck_id": str(precheck.id),
                "request_id": request_id,
                "storage_key": document.storage_key,
                "error_code": code,
                "issue_code": issue_code,
            },
        )
        self._finish_without_ai(
            precheck,
            document=document,
            issues=[build_issue(issue_code, metadata={"errorCode": code})],
            status_value="error",
            decision="failed",
            traffic_light="red",
            summary=message,
            confidence_score=Decimal("0.0000"),
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
            error_code=code,
            error_message=message,
            request_id=request_id,
        )

    def _finish_provider_failure(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document,
        code: str,
        issue_code: str,
        message: str,
        details: dict[str, Any] | None = None,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None = None,
    ) -> None:
        issue = build_issue(issue_code, message=message, metadata={"errorCode": code, **(details or {})})
        logger.exception(
            "AI provider failure during document precheck",
            extra={
                "case_id": str(precheck.case_id),
                "document_id": str(document.id),
                "precheck_id": str(precheck.id),
                "request_id": request_id,
                "storage_key": document.storage_key,
                "error_code": code,
                "issue_code": issue_code,
                "provider_status_code": (details or {}).get("statusCode"),
                "provider_message": (details or {}).get("providerMessage"),
                "provider_response_body": (details or {}).get("responseBody"),
            },
        )
        self._finish_without_ai(
            precheck,
            document=document,
            issues=[issue],
            status_value="error",
            decision="failed",
            traffic_light="red",
            summary=message,
            confidence_score=Decimal("0.0000"),
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
            error_code=code,
            error_message=message,
            request_id=request_id,
        )

    def _audit_terminal(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document,
        actor: User,
        previous_state: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if precheck.status == "completed":
            event = "ia_documental_preliminar.completed"
        elif precheck.status == "blocked":
            event = "ia_documental_preliminar.blocked"
        elif precheck.status == "requires_review":
            event = "ia_documental_preliminar.requires_review"
        else:
            event = "ia_documental_preliminar.failed"
        self._audit(
            event,
            precheck=precheck,
            document=document,
            actor=actor,
            previous_state=previous_state,
            new_state=_precheck_state(precheck),
            metadata={
                "provider": precheck.provider,
                "model": precheck.model,
                "confidenceScore": _float(precheck.confidence_score),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _document_for_case_or_error(self, *, case_id: str, document_id: str) -> tuple[LaboraCase, Document]:
        case = self._get_case_or_404(case_id)
        document = self._get_document_or_404(document_id)
        if document.case_id != case.id:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="DOCUMENT_NOT_IN_CASE",
                message="El documento no pertenece a este expediente.",
            )
        return case, document

    def _require_document_available(self, document: Document) -> None:
        if document.status not in COMPLETE_DOCUMENT_STATUSES or document.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="DOCUMENT_NOT_FOUND",
                message="El documento no esta disponible para prechequeo.",
            )

    def _require_sensitive_consent(self, user: User) -> None:
        permission = ConsentComplianceService(self.db).can_upload_documents(user.id)
        if permission.allowed:
            return
        raise ApiError(
            status_code=422,
            code="CONSENT_REQUIRED",
            message="Debes aceptar la autorizacion de tratamiento de datos antes de procesar documentos.",
            details={
                "missingConsentTypes": permission.missing_consent_types,
                "reason": permission.reason,
            },
        )

    def _require_can_view_case(self, case: LaboraCase, user: User) -> None:
        if self._can_view_case(case, user):
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="UNAUTHORIZED",
            message="No tienes permisos para acceder a este expediente.",
        )

    def _require_can_update_case(self, case: LaboraCase, user: User) -> None:
        if self._can_update_case(case, user):
            return
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="UNAUTHORIZED",
            message="No tienes permisos para modificar este expediente.",
        )

    def _can_view_case(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
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

    def _can_update_case(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
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

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(status_code=404, code="CASE_NOT_FOUND", message="Expediente no encontrado.")
        return case

    def _get_document_or_404(self, document_id: str | uuid.UUID) -> Document:
        document = self.documents.get(document_id)
        if document is None:
            raise ApiError(status_code=404, code="DOCUMENT_NOT_FOUND", message="Documento no encontrado.")
        return document

    def _get_precheck_or_404(self, precheck_id: str | uuid.UUID) -> DocumentPrecheck:
        precheck = self.prechecks.get(precheck_id)
        if precheck is None:
            raise self._precheck_not_found()
        return precheck

    def _precheck_not_found(self) -> ApiError:
        return ApiError(status_code=404, code="DOCUMENT_NOT_FOUND", message="Prechequeo no encontrado.")

    def _precheck_detail(
        self,
        precheck: DocumentPrecheck,
        *,
        document: Document | None,
        include_pages: bool = False,
    ) -> dict[str, Any]:
        ocr_job = precheck.ocr_jobs[-1] if precheck.ocr_jobs else self.prechecks.latest_ocr_job(precheck.document_id)
        ai = precheck.ai_confidences[-1] if precheck.ai_confidences else None
        return {
            "precheckId": str(precheck.id),
            "caseId": str(precheck.case_id),
            "documentId": str(precheck.document_id),
            "documentName": document.original_filename if document else None,
            "status": precheck.status,
            "decision": precheck.decision,
            "trafficLight": precheck.traffic_light,
            "confidenceScore": _float(precheck.confidence_score),
            "summary": precheck.summary,
            "issues": [_issue_detail(issue) for issue in precheck.issues],
            "ocr": self._ocr_summary(ocr_job, include_pages=include_pages) if ocr_job else None,
            "ai": {
                "provider": ai.provider,
                "model": ai.model,
                "task": ai.task,
                "confidenceScore": _float(ai.score),
                "latencyMs": ai.latency_ms,
            }
            if ai
            else None,
            "createdAt": precheck.created_at,
            "updatedAt": precheck.updated_at,
            "startedAt": precheck.started_at,
            "completedAt": precheck.completed_at,
            "failedAt": precheck.failed_at,
            "links": {
                "self": f"/api/v1/cases/{precheck.case_id}/document-precheck/{precheck.id}",
                "poll": f"/api/v1/cases/{precheck.case_id}/document-precheck",
            },
        }

    def _ocr_summary(self, ocr_job: OcrJob, *, include_pages: bool) -> dict[str, Any]:
        payload = {
            "ocrJobId": str(ocr_job.id),
            "status": ocr_job.status,
            "engine": ocr_job.engine,
            "pagesTotal": ocr_job.pages_total,
            "pagesProcessed": ocr_job.pages_processed,
            "textDetected": ocr_job.text_detected,
            "avgTextDensity": _float(ocr_job.avg_text_density),
        }
        if include_pages:
            payload["pages"] = [
                {
                    "pageNumber": page.page_number,
                    "textPreview": page.text_preview,
                    "confidenceScore": _float(page.confidence_score),
                    "textDensity": _float(page.text_density),
                    "isBlurry": page.is_blurry,
                    "isRotated": page.is_rotated,
                    "rotationDegrees": page.rotation_degrees,
                    "hasTableLikeContent": page.has_table_like_content,
                    "issues": [
                        {
                            "code": issue.get("code"),
                            "severity": issue.get("severity"),
                            "pageNumber": issue.get("page_number"),
                            "title": issue.get("title"),
                            "message": issue.get("message"),
                            "suggestedAction": issue.get("suggested_action"),
                        }
                        for issue in (page.issues_json or [])
                    ],
                }
                for page in sorted(ocr_job.pages, key=lambda item: item.page_number)
            ]
        return payload

    def _audit(
        self,
        event_name: str,
        *,
        precheck: DocumentPrecheck | None,
        document: Document | None,
        actor: User | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        event_metadata = {
            "caseId": str((precheck.case_id if precheck else None) or (document.case_id if document else None)),
            "documentId": str((precheck.document_id if precheck else None) or (document.id if document else None)),
            "precheckId": str(precheck.id) if precheck else None,
            "actorType": _actor_type(actor),
            "actorId": str(actor.id) if actor else None,
            "sourceModule": "ia_documental_preliminar",
        }
        if metadata:
            event_metadata.update(metadata)
        self.audit.create(
            event_type=event_name,
            entity_type="document_precheck",
            entity_id=precheck.id if precheck else None,
            actor_user_id=actor.id if actor else None,
            previous_state=previous_state,
            new_state=new_state,
            metadata=event_metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def _classification_input(
    *,
    document: Document,
    ocr_job: OcrJob,
    precheck_id: uuid.UUID,
    request_id: str,
) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    remaining_chars = AI_TOTAL_TEXT_PREVIEW_CHARS
    for page in sorted(ocr_job.pages, key=lambda item: item.page_number):
        raw_preview = page.text_preview or ""
        page_limit = min(AI_PAGE_TEXT_PREVIEW_CHARS, max(remaining_chars, 0))
        limited_preview = _limited_text_fragment(raw_preview, limit=page_limit)
        remaining_chars -= len(limited_preview)
        pages.append(
            {
                "pageNumber": page.page_number,
                "textPreview": limited_preview,
                "textPreviewChars": len(limited_preview),
                "textPreviewOriginalChars": len(raw_preview),
                "textPreviewTruncated": len(limited_preview) < len(raw_preview),
                "confidenceScore": _float(page.confidence_score),
                "textDensity": _float(page.text_density),
                "isBlurry": page.is_blurry,
                "isRotated": page.is_rotated,
                "hasTableLikeContent": page.has_table_like_content,
                "detectedLabels": page.detected_labels or [],
            }
        )
        if remaining_chars <= 0:
            break
    return {
        "requestId": request_id,
        "precheckId": str(precheck_id),
        "caseId": str(document.case_id),
        "documentId": str(document.id),
        "fileMetadata": {
            "mimeType": document.mime_type,
            "pagesTotal": ocr_job.pages_total,
            "sizeBytes": document.size_bytes,
        },
        "ocrSignals": {
            "textDetected": ocr_job.text_detected,
            "avgTextDensity": _float(ocr_job.avg_text_density),
            "pages": pages,
        },
        "allowedDocumentTypes": ALLOWED_DOCUMENT_TYPES,
    }


def _issues_from_ocr_job(ocr_job: OcrJob) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for page in ocr_job.pages:
        for issue in page.issues_json or []:
            issues.append(issue)
    if not ocr_job.text_detected:
        issues.append(build_issue("no_text_detected", severity="critical"))
    if ocr_job.avg_text_density is not None and Decimal(ocr_job.avg_text_density) < Decimal("0.10"):
        issues.append(build_issue("ocr_low_confidence", severity="critical"))
    return _dedupe_issues(issues)


def _decision_from_result(
    *,
    traffic_light: str,
    confidence_score: float,
    is_suitable: bool,
    issues: list[dict[str, Any]],
) -> str:
    if any(issue["severity"] == "critical" for issue in issues):
        unsupported_codes = {
            "file_unsupported",
            "wrong_document_type",
            "not_labor_or_pension_document",
        }
        if any(issue["code"] in unsupported_codes for issue in issues):
            return "unsupported"
        reupload_codes = {
            "pdf_password_protected",
            "pdf_corrupted",
            "no_text_detected",
            "ocr_low_confidence",
            "ocr_provider_not_configured",
        }
        if any(issue["code"] in reupload_codes for issue in issues):
            return "requires_reupload"
        return "requires_human_review" if is_suitable else "requires_reupload"
    if not is_suitable:
        return "requires_human_review"
    if traffic_light == "green" and confidence_score >= 0.85:
        return "suitable"
    if confidence_score >= 0.65:
        return "suitable_with_observations"
    return "requires_human_review"


def _traffic_light(raw: str, issues: list[dict[str, Any]], confidence_score: float) -> str:
    if any(issue["severity"] == "critical" for issue in issues) or confidence_score < 0.65:
        return "red"
    if raw == "green" and confidence_score >= 0.85 and not issues:
        return "green"
    if confidence_score >= 0.65:
        return "yellow"
    return "gray"


def _issue_detail(issue) -> dict[str, Any]:
    return {
        "code": issue.code,
        "severity": issue.severity,
        "pageNumber": issue.page_number,
        "title": issue.title,
        "message": issue.message,
        "suggestedAction": issue.suggested_action,
        "metadata": issue.metadata_json,
    }


def _precheck_state(precheck: DocumentPrecheck) -> dict[str, Any]:
    return {
        "status": precheck.status,
        "decision": precheck.decision,
        "trafficLight": precheck.traffic_light,
        "confidenceScore": _float(precheck.confidence_score),
    }


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int | None]] = set()
    deduped: list[dict[str, Any]] = []
    for issue in issues:
        key = (issue["code"], issue.get("page_number"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _limited_text_fragment(text: str, *, limit: int) -> str:
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    if limit < 120:
        return text[:limit]
    head_size = max(80, int(limit * 0.70))
    tail_size = max(40, limit - head_size - 8)
    return f"{text[:head_size]}\n...\n{text[-tail_size:]}"


def _ocr_characters(ocr_job: OcrJob) -> int:
    return sum(len(page.text_preview or "") for page in ocr_job.pages)


def _float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _actor_type(actor: User | None) -> str:
    if actor is None:
        return "system"
    if actor.role in ADMIN_ROLES:
        return "admin"
    if actor.role in ADMIN_REVIEW_ROLES:
        return "admin"
    return "user"
