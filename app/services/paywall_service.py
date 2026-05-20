import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.core.config import settings
from app.models.case import LaboraCase
from app.models.paywall import Paywall, PreviewResult
from app.models.pre_analysis import PreAnalysis
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.paywall_repository import PaywallRepository
from app.repositories.pre_analysis_repository import PreAnalysisRepository
from app.services.case_state_machine import (
    has_confirmed_payment,
    step_for_status,
    validate_case_transition,
)
from app.services.consent_service import ConsentComplianceService
from app.services.epayco_service import (
    EpaycoCheckoutClient,
    EpaycoProviderError,
    epayco_confirmation_signature,
    paywall_id_from_epayco_invoice,
)
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
PREVIEW_ADMIN_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, "legal_ops", "reviewer"}
LOCKED_CASE_STATUSES = {"closed", "archived", "blocked"}
UNLOCKED_CASE_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "completed",
}
READY_PREANALYSIS_STATUSES = {"completed", "requires_review"}
LOW_CONFIDENCE_THRESHOLD = Decimal("0.7000")
PROMPT_VERSION = "paywall-preview-v1"
DEFAULT_AI_MODEL = "rules-paywall-preview-v1"
PAYMENT_PRODUCT_CODE = "ANALISIS_COMPLETO_HISTORIA_LABORAL"
PRICE_AMOUNT = Decimal(str(max(settings.full_analysis_unlock_price_cop, 0)))
PRICE_CURRENCY = (settings.payment_currency or "COP").strip().upper()
PRICE_LABEL = f"${int(PRICE_AMOUNT):,} {PRICE_CURRENCY}".replace(",", ".")

PAYWALL_EVENTS = {
    "created": "vista_previa_resultado_paywall.created",
    "updated": "vista_previa_resultado_paywall.updated",
    "viewed": "vista_previa_resultado_paywall.viewed",
    "submitted": "vista_previa_resultado_paywall.submitted",
    "approved": "vista_previa_resultado_paywall.approved",
    "rejected": "vista_previa_resultado_paywall.rejected",
    "failed": "vista_previa_resultado_paywall.failed",
}

DEFAULT_LOCKED_FEATURES = [
    {
        "key": "technical_report",
        "title": "Informe tecnico completo",
        "description": "Desbloquea el analisis detallado y sustentado.",
        "isHighlighted": True,
    },
    {
        "key": "calculation_breakdown",
        "title": "Calculo y diferencia estimada",
        "description": "Revisa el escenario economico completo.",
        "isHighlighted": True,
    },
    {
        "key": "inconsistency_matrix",
        "title": "Matriz de inconsistencias",
        "description": "Consulta los cruces y hallazgos detallados.",
        "isHighlighted": True,
    },
    {
        "key": "recommended_route",
        "title": "Ruta recomendada",
        "description": "Recibe una orientacion completa segun los soportes.",
        "isHighlighted": False,
    },
    {
        "key": "legal_document_draft",
        "title": "Escritos si aplican",
        "description": "Accede a borradores solo cuando el caso lo permita.",
        "isHighlighted": False,
    },
]

BLURRED_SECTIONS = [
    "Matriz de inconsistencias",
    "Calculo estimado",
    "Fundamento juridico",
    "Ruta recomendada",
]

COMPARISON = {
    "free": [
        "Resumen preliminar limitado",
        "Nivel de completitud",
        "Alertas generales",
    ],
    "paid": [
        "Informe completo",
        "Matriz de inconsistencias",
        "Calculo detallado",
        "Recomendacion de ruta",
        "Descargas y escritos si aplican",
    ],
}

PROHIBITED_TEXT_PATTERNS = [
    re.compile(r"\bganar[aá]?\b", flags=re.IGNORECASE),
    re.compile(r"\bgarantizad[oa]s?\b", flags=re.IGNORECASE),
    re.compile(r"\basegurad[oa]s?\b", flags=re.IGNORECASE),
    re.compile(r"\b[eé]xito judicial\b", flags=re.IGNORECASE),
    re.compile(r"\bpago seguro\b", flags=re.IGNORECASE),
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


class PaywallPreviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.pre_analysis = PreAnalysisRepository(db)
        self.paywalls = PaywallRepository(db)
        self.audit_events = AuditEventRepository(db)

    def create_or_refresh(
        self,
        case_id: str,
        *,
        force_refresh: bool,
        reason: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_update(case, user, ip_address, user_agent)
        self._require_case_not_blocked(case)
        self._require_consents(case.owner_user_id)
        pre_analysis = self._require_ready_preanalysis(case)
        preview, paywall, created = self._ensure_preview(
            case=case,
            pre_analysis=pre_analysis,
            actor=user,
            force_refresh=force_refresh,
            reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._sync_paywall_unlock_state(case, preview, paywall)
        self._audit(
            PAYWALL_EVENTS["submitted"],
            actor=user,
            case=case,
            preview=preview,
            previous_state=None,
            new_state=self._preview_state(preview),
            metadata={"reason": reason, "created": created},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "caseId": str(case.id),
            "previewId": str(preview.id),
            "status": preview.status,
            "message": "La vista previa se preparo correctamente.",
        }

    def get_preview(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_case_not_blocked(case)
        self._require_consents(case.owner_user_id)
        pre_analysis = self._require_ready_preanalysis(case)
        preview, paywall, _created = self._ensure_preview(
            case=case,
            pre_analysis=pre_analysis,
            actor=user,
            force_refresh=False,
            reason="view",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._sync_paywall_unlock_state(case, preview, paywall)
        self._record_conversion_event(
            event_name="paywall_viewed",
            source="web",
            case_id=case.id,
            user_id=user.id,
            metadata={"previewId": str(preview.id), "isUnlocked": self._is_unlocked(case, paywall)},
        )
        self._audit(
            PAYWALL_EVENTS["viewed"],
            actor=user,
            case=case,
            preview=preview,
            metadata={"surface": "preview"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._preview_payload(case=case, preview=preview, paywall=paywall)

    def get_paywall_config(
        self,
        case_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_case_not_blocked(case)
        self._require_consents(case.owner_user_id)
        pre_analysis = self._require_ready_preanalysis(case)
        preview, paywall, _created = self._ensure_preview(
            case=case,
            pre_analysis=pre_analysis,
            actor=user,
            force_refresh=False,
            reason="paywall_config",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._sync_paywall_unlock_state(case, preview, paywall)
        self._audit(
            PAYWALL_EVENTS["viewed"],
            actor=user,
            case=case,
            preview=preview,
            metadata={"surface": "paywall_config"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._paywall_config_payload(case=case, paywall=paywall)

    def create_checkout_session(
        self,
        case_id: str,
        *,
        source: str,
        return_url: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_case_not_blocked(case)
        self._require_consents(case.owner_user_id)
        pre_analysis = self._require_ready_preanalysis(case)
        preview, paywall, _created = self._ensure_preview(
            case=case,
            pre_analysis=pre_analysis,
            actor=user,
            force_refresh=False,
            reason="checkout",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._sync_paywall_unlock_state(case, preview, paywall)
        if self._is_unlocked(case, paywall):
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYWALL_ALREADY_UNLOCKED",
                message="El analisis completo ya esta desbloqueado.",
            )
        if return_url and not return_url.lower().startswith(("http://", "https://")):
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="CHECKOUT_RETURN_URL_INVALID",
                message="returnUrl debe ser una URL http o https.",
            )

        try:
            checkout_session = EpaycoCheckoutClient().create_session(
                case=case,
                preview=preview,
                paywall=paywall,
                return_url=return_url,
            )
        except EpaycoProviderError as exc:
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="CHECKOUT_UNAVAILABLE",
                message="No fue posible iniciar el checkout con ePayco.",
                details={"reason": exc.code},
            ) from exc

        paywall.checkout_url = checkout_session.checkout_url
        paywall.updated_at = utc_now()
        self._record_conversion_event(
            event_name="checkout_started",
            source="web",
            case_id=case.id,
            user_id=user.id,
            metadata={
                "provider": checkout_session.provider,
                "checkoutSessionId": checkout_session.session_id,
                "invoice": checkout_session.invoice,
                "source": source,
                "returnUrl": return_url,
                "previewId": str(preview.id),
            },
        )
        self._audit(
            PAYWALL_EVENTS["updated"],
            actor=user,
            case=case,
            preview=preview,
            metadata={
                "action": "checkout_started",
                "provider": checkout_session.provider,
                "checkoutSessionId": checkout_session.session_id,
                "invoice": checkout_session.invoice,
                "epaycoPayload": _checkout_audit_payload(checkout_session.provider_payload),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "checkoutSessionId": checkout_session.session_id,
            "checkoutUrl": checkout_session.checkout_url,
            "expiresAt": checkout_session.expires_at,
            "provider": "epayco",
            "checkoutType": settings.epayco_checkout_type,
            "testMode": settings.epayco_test_mode,
            "providerSessionToken": checkout_session.provider_session_token,
        }

    def confirm_epayco_payment(
        self,
        *,
        payload: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        expected_signature = epayco_confirmation_signature(payload)
        received_signature = payload.get("x_signature")
        if expected_signature and received_signature != expected_signature:
            raise ApiError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="EPAYCO_SIGNATURE_INVALID",
                message="La firma de confirmacion de ePayco no es valida.",
            )

        paywall_id = paywall_id_from_epayco_invoice(
            payload.get("x_id_invoice") or payload.get("invoice"),
        )
        if paywall_id is None and payload.get("x_extra2"):
            try:
                paywall_id = uuid.UUID(str(payload.get("x_extra2")))
            except (TypeError, ValueError):
                paywall_id = None
        if paywall_id is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="PAYWALL_NOT_FOUND",
                message="No se encontro el paywall asociado a la confirmacion de ePayco.",
            )
        paywall = self.db.get(Paywall, paywall_id)
        if paywall is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="PAYWALL_NOT_FOUND",
                message="No se encontro el paywall asociado a la confirmacion de ePayco.",
            )
        case = self._get_case_or_404(paywall.case_id)
        preview = self._get_preview_or_404(paywall.preview_result_id)
        response_code = str(payload.get("x_cod_response") or "").strip()
        response_text = str(payload.get("x_response") or "").strip().lower()
        accepted = response_code == "1" or response_text == "aceptada"

        self._record_conversion_event(
            event_name="checkout_returned",
            source="system",
            case_id=case.id,
            user_id=paywall.user_id,
            metadata=_epayco_public_metadata(payload),
        )
        if accepted:
            previous_paywall = {
                "status": paywall.status,
                "unlockRequired": paywall.unlock_required,
                "unlockedAt": paywall.unlocked_at,
            }
            now = utc_now()
            paywall.status = "completed"
            paywall.unlock_required = False
            paywall.unlocked_at = paywall.unlocked_at or now
            paywall.unlocked_by_payment_id = _payment_uuid(payload)
            paywall.updated_at = now
            previous_case_status = case.status
            if not self._case_is_unlocked(case):
                validate_case_transition(
                    self.db,
                    case,
                    new_status="paid_unlocked",
                    validate_transition=True,
                )
                case.status = "paid_unlocked"
                case.status_reason = "Pago confirmado por ePayco."
                case.current_step, case.next_best_action = step_for_status("paid_unlocked")
                case.updated_at = now
                self.cases.create_status_history(
                    case_id=case.id,
                    previous_status=previous_case_status,
                    new_status=case.status,
                    reason="Pago confirmado por ePayco.",
                    changed_by_user_id=None,
                    changed_by_role="system",
                    source_module="payments",
                    metadata={"provider": "epayco", "invoice": payload.get("x_id_invoice")},
                )
            self._record_conversion_event(
                event_name="unlock_completed",
                source="system",
                case_id=case.id,
                user_id=paywall.user_id,
                metadata=_epayco_public_metadata(payload),
            )
            self._audit(
                PAYWALL_EVENTS["updated"],
                actor=None,
                case=case,
                preview=preview,
                previous_state=previous_paywall,
                new_state={
                    "status": paywall.status,
                    "unlockRequired": paywall.unlock_required,
                    "unlockedAt": paywall.unlocked_at,
                },
                metadata={
                    "action": "epayco_unlock_completed",
                    **_epayco_public_metadata(payload),
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

        self.db.commit()
        return {
            "stored": True,
            "accepted": accepted,
            "paywallId": str(paywall.id),
            "caseId": str(case.id),
            "status": paywall.status,
        }

    def record_conversion_event(
        self,
        *,
        case_id: str | None,
        event_name: str,
        source: str,
        metadata: dict[str, Any],
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        parsed_case_id: uuid.UUID | None = None
        if case_id:
            case = self._get_case_or_404(case_id)
            self._require_can_view(case, user, ip_address, user_agent)
            parsed_case_id = case.id
        event = self._record_conversion_event(
            event_name=event_name,
            source=source,
            case_id=parsed_case_id,
            user_id=user.id,
            metadata=_safe_metadata(metadata),
        )
        self.audit_events.create(
            event_type="conversion_event.created",
            entity_type="conversion_event",
            entity_id=event.id,
            actor_user_id=user.id,
            metadata=_json_safe(
                {
                    "caseId": str(parsed_case_id) if parsed_case_id else None,
                    "eventName": event_name,
                    "source": source,
                    "sourceModule": "paywall",
                }
            ),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"eventId": str(event.id), "stored": True}

    def list_admin_previews(
        self,
        *,
        status_filter: str | None,
        page: int,
        page_size: int,
        user: User,
    ) -> dict[str, Any]:
        self._require_admin_role(user)
        items, total = self.paywalls.list_admin_previews(
            status_filter=status_filter,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._admin_item(item) for item in items],
            "pagination": {"page": page, "pageSize": page_size, "total": total},
        }

    def approve_preview(
        self,
        preview_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin_role(user)
        preview = self._get_preview_or_404(preview_id)
        case = self._get_case_or_404(preview.case_id)
        paywall = self._ensure_paywall_for_preview(case=case, preview=preview)
        previous_state = self._preview_state(preview)
        preview.status = "completed"
        preview.requires_human_review = False
        preview.reviewed_by = user.id
        preview.reviewed_at = utc_now()
        preview.review_notes = "Aprobado para mostrar vista previa."
        preview.updated_at = preview.reviewed_at
        if not self._is_unlocked(case, paywall):
            paywall.status = "blocked"
            paywall.unlock_required = True
            paywall.updated_at = utc_now()
            self._transition_case(
                case,
                new_status="preview_locked",
                actor=user,
                reason="Preview aprobado por revision interna.",
            )
        self._audit(
            PAYWALL_EVENTS["approved"],
            actor=user,
            case=case,
            preview=preview,
            previous_state=previous_state,
            new_state=self._preview_state(preview),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._preview_payload(case=case, preview=preview, paywall=paywall)

    def reject_preview(
        self,
        preview_id: str,
        *,
        reason: str,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        self._require_admin_role(user)
        preview = self._get_preview_or_404(preview_id)
        case = self._get_case_or_404(preview.case_id)
        paywall = self._ensure_paywall_for_preview(case=case, preview=preview)
        previous_state = self._preview_state(preview)
        preview.status = "requires_review"
        preview.requires_human_review = True
        preview.reviewed_by = user.id
        preview.reviewed_at = utc_now()
        preview.review_notes = _safe_text(reason, limit=1000)
        preview.updated_at = preview.reviewed_at
        paywall.status = "requires_review"
        paywall.updated_at = utc_now()
        if has_confirmed_payment(self.db, case):
            self._transition_case(
                case,
                new_status="requires_review",
                actor=user,
                reason="Preview devuelto por revision interna.",
            )
        else:
            self._transition_case(
                case,
                new_status="preview_locked",
                actor=user,
                reason="Preview devuelto por revision interna antes de pago confirmado.",
            )
        self._audit(
            PAYWALL_EVENTS["rejected"],
            actor=user,
            case=case,
            preview=preview,
            previous_state=previous_state,
            new_state=self._preview_state(preview),
            metadata={"reason": preview.review_notes},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._preview_payload(case=case, preview=preview, paywall=paywall)

    def _ensure_preview(
        self,
        *,
        case: LaboraCase,
        pre_analysis: PreAnalysis,
        actor: User,
        force_refresh: bool,
        reason: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[PreviewResult, Paywall, bool]:
        if not force_refresh:
            existing = self.paywalls.latest_preview_for_preanalysis(
                case_id=case.id,
                source_preanalysis_id=pre_analysis.id,
            )
            if existing is not None:
                paywall = self._ensure_paywall_for_preview(case=case, preview=existing)
                return existing, paywall, False

        preview = self._generate_preview(
            case=case,
            pre_analysis=pre_analysis,
            actor=actor,
            reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        paywall = self._ensure_paywall_for_preview(case=case, preview=preview)
        return preview, paywall, True

    def _generate_preview(
        self,
        *,
        case: LaboraCase,
        pre_analysis: PreAnalysis,
        actor: User,
        reason: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> PreviewResult:
        requires_review = self._requires_human_review(pre_analysis)
        preview_status = "requires_review" if requires_review else "completed"
        title, limited_text, hidden_hint, teaser = self._preview_texts(pre_analysis, requires_review)
        input_payload = {
            "caseId": str(case.id),
            "preAnalysisId": str(pre_analysis.id),
            "status": pre_analysis.status,
            "completionScore": _json_safe(pre_analysis.completion_score),
            "confidence": _json_safe(pre_analysis.confidence),
            "trafficLight": pre_analysis.traffic_light,
            "viabilityLevel": pre_analysis.viability_level,
            "reason": reason,
        }
        output_payload = {
            "title": title,
            "limitedText": limited_text,
            "hiddenValueHint": hidden_hint,
            "mainFindingTeaser": teaser,
            "status": preview_status,
        }
        now = utc_now()
        preview = self.paywalls.create_preview(
            case_id=case.id,
            user_id=case.owner_user_id,
            status=preview_status,
            summary_title=title,
            summary_limited=limited_text,
            alert_level=self._alert_level(pre_analysis, requires_review),
            completion_score=_completion_score_or_none(pre_analysis.completion_score),
            hidden_value_hint=hidden_hint,
            main_finding_teaser=teaser,
            confidence_score=_confidence_or_none(pre_analysis.confidence),
            requires_human_review=requires_review,
            ai_model=pre_analysis.ai_model or DEFAULT_AI_MODEL,
            ai_prompt_version=PROMPT_VERSION,
            input_hash=_stable_hash(input_payload),
            output_hash=_stable_hash(output_payload),
            source_preanalysis_id=pre_analysis.id,
            created_at=now,
            updated_at=now,
        )
        self._audit(
            PAYWALL_EVENTS["created"],
            actor=actor,
            case=case,
            preview=preview,
            new_state=self._preview_state(preview),
            metadata={"reason": reason, "sourcePreanalysisId": str(pre_analysis.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._record_history_event(
            case=case,
            actor=actor,
            event_type=PAYWALL_EVENTS["created"],
            title="Vista previa preparada",
            description="Se preparo la vista previa limitada del resultado.",
            severity="warning" if requires_review else "success",
            metadata={"previewId": str(preview.id), "requiresHumanReview": requires_review},
        )
        if requires_review:
            if has_confirmed_payment(self.db, case):
                self._transition_case(
                    case,
                    new_status="requires_review",
                    actor=actor,
                    reason="La vista previa requiere revision humana.",
                )
            elif not self._case_is_unlocked(case):
                self._transition_case(
                    case,
                    new_status="preview_locked",
                    actor=actor,
                    reason="Vista previa requiere revision humana antes del pago confirmado.",
                )
        elif not self._case_is_unlocked(case):
            self._transition_case(
                case,
                new_status="preview_locked",
                actor=actor,
                reason="Vista previa disponible antes del pago.",
            )
        return preview

    def _ensure_paywall_for_preview(
        self,
        *,
        case: LaboraCase,
        preview: PreviewResult,
    ) -> Paywall:
        paywall = self.paywalls.paywall_for_preview(preview.id)
        if paywall is None:
            is_unlocked = self._case_is_unlocked(case)
            status_value = (
                "completed"
                if is_unlocked
                else "requires_review"
                if preview.status == "requires_review"
                else "blocked"
            )
            paywall = self.paywalls.create_paywall(
                case_id=case.id,
                user_id=case.owner_user_id,
                preview_result_id=preview.id,
                status=status_value,
                unlock_required=not is_unlocked,
                unlock_type="payment",
                payment_product_code=PAYMENT_PRODUCT_CODE,
                price_amount=PRICE_AMOUNT,
                price_currency=PRICE_CURRENCY,
                price_label=PRICE_LABEL,
                unlocked_at=utc_now() if is_unlocked else None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.paywalls.replace_locked_features(
                paywall_id=paywall.id,
                features=DEFAULT_LOCKED_FEATURES,
            )
            return paywall

        if not self.paywalls.list_locked_features(paywall.id):
            self.paywalls.replace_locked_features(
                paywall_id=paywall.id,
                features=DEFAULT_LOCKED_FEATURES,
            )
        return paywall

    def _sync_paywall_unlock_state(
        self,
        case: LaboraCase,
        preview: PreviewResult,
        paywall: Paywall,
    ) -> None:
        if not self._case_is_unlocked(case):
            if preview.status == "requires_review":
                paywall.status = "requires_review"
            elif paywall.unlocked_at is None:
                paywall.status = "blocked"
                paywall.unlock_required = True
            paywall.updated_at = utc_now()
            return
        if not self._is_unlocked(case, paywall):
            paywall.unlocked_at = utc_now()
        paywall.status = "completed"
        paywall.unlock_required = False
        paywall.updated_at = utc_now()

    def _preview_payload(
        self,
        *,
        case: LaboraCase,
        preview: PreviewResult,
        paywall: Paywall,
    ) -> dict[str, Any]:
        is_unlocked = self._is_unlocked(case, paywall)
        features = self.paywalls.list_locked_features(paywall.id)
        return {
            "caseId": str(case.id),
            "previewId": str(preview.id),
            "status": preview.status,
            "isUnlocked": is_unlocked,
            "summary": {
                "title": preview.summary_title,
                "limitedText": preview.summary_limited,
                "mainFindingTeaser": preview.main_finding_teaser,
                "alertLevel": preview.alert_level or "unknown",
                "completionScore": _float(preview.completion_score),
                "confidenceScore": _float(preview.confidence_score),
                "requiresHumanReview": preview.requires_human_review,
                "hiddenValueHint": preview.hidden_value_hint,
            },
            "lockedContent": {
                "blurredSections": BLURRED_SECTIONS,
                "features": [
                    {
                        "key": feature.feature_key,
                        "title": feature.title,
                        "description": feature.description,
                        "isHighlighted": feature.is_highlighted,
                    }
                    for feature in features
                ],
            },
            "cta": self._cta(case=case, preview=preview, paywall=paywall),
            "comparison": COMPARISON,
            "warnings": self._warnings(preview),
        }

    def _paywall_config_payload(
        self,
        *,
        case: LaboraCase,
        paywall: Paywall,
    ) -> dict[str, Any]:
        return {
            "caseId": str(case.id),
            "paywallId": str(paywall.id),
            "unlockRequired": paywall.unlock_required,
            "unlockType": paywall.unlock_type,
            "status": paywall.status,
            "paymentProductCode": paywall.payment_product_code,
            "price": {
                "amount": float(paywall.price_amount or 0),
                "currency": paywall.price_currency or PRICE_CURRENCY,
                "label": paywall.price_label or PRICE_LABEL,
            },
            "unlockedAt": paywall.unlocked_at,
        }

    def _cta(
        self,
        *,
        case: LaboraCase,
        preview: PreviewResult,
        paywall: Paywall,
    ) -> dict[str, Any]:
        disclaimer = (
            "El pago desbloquea el analisis completo, no garantiza un resultado "
            "administrativo o judicial."
        )
        if self._is_unlocked(case, paywall):
            return {
                "label": "Ver analisis completo",
                "target": "analysis",
                "checkoutUrl": None,
                "priceLabel": paywall.price_label or PRICE_LABEL,
                "disclaimer": disclaimer,
            }
        if preview.status == "requires_review" or preview.requires_human_review:
            return {
                "label": "Desbloquear analisis completo",
                "target": "checkout",
                "checkoutUrl": paywall.checkout_url or f"/app/cases/{case.id}/checkout",
                "priceLabel": paywall.price_label or PRICE_LABEL,
                "disclaimer": (
                    "El pago desbloquea el analisis completo y el caso puede mantener"
                    " validaciones internas adicionales."
                ),
            }
        return {
            "label": "Desbloquear analisis completo",
            "target": "checkout",
            "checkoutUrl": paywall.checkout_url or f"/app/cases/{case.id}/checkout",
            "priceLabel": paywall.price_label or PRICE_LABEL,
            "disclaimer": disclaimer,
        }

    def _warnings(self, preview: PreviewResult) -> list[dict[str, str]]:
        warnings = [
            {
                "code": "PRELIMINARY_ONLY",
                "message": "Este resultado es preliminar y no reemplaza el analisis completo.",
            },
            {
                "code": "PAYMENT_UNLOCKS_ANALYSIS_ONLY",
                "message": "El pago no garantiza un resultado administrativo o judicial.",
            },
        ]
        if preview.status == "requires_review" or preview.requires_human_review:
            warnings.append(
                {
                    "code": "LOW_CONFIDENCE_REVIEW",
                    "message": "El caso requiere revision humana antes de afirmaciones comerciales.",
                }
            )
        return warnings

    def _preview_texts(
        self,
        pre_analysis: PreAnalysis,
        requires_review: bool,
    ) -> tuple[str, str, str, str | None]:
        if requires_review:
            return (
                "Tu caso requiere revision antes de conclusiones",
                "La informacion disponible permite una orientacion inicial, pero necesita validacion antes de mostrar mas detalles.",
                "La revision completa puede ayudar a ordenar soportes, alertas y escenarios sin prometer un resultado.",
                "Hay senales preliminares que deben confirmarse con revision interna.",
            )
        issues = self.pre_analysis.list_visible_issues(pre_analysis.id)
        first_issue = issues[0] if issues else None
        title = _safe_text(
            pre_analysis.value_detected_title
            or "Encontramos senales que podrian requerir revision",
            limit=180,
        )
        limited_text = _safe_text(
            pre_analysis.limited_summary
            or "Tu historia laboral presenta indicios preliminares que justifican un analisis completo.",
            limit=700,
        )
        hidden_hint = _safe_text(
            pre_analysis.value_detected_summary
            or "El analisis completo puede ayudarte a entender diferencias relevantes y pasos posibles.",
            limit=400,
        )
        teaser = None
        if first_issue is not None:
            teaser = _safe_text(
                first_issue.public_summary or first_issue.title,
                limit=240,
            )
        return title, limited_text, hidden_hint, teaser

    def _alert_level(self, pre_analysis: PreAnalysis, requires_review: bool) -> str:
        if requires_review:
            return "unknown"
        return {
            "green": "low",
            "yellow": "medium",
            "red": "high",
            "gray": "unknown",
        }.get(pre_analysis.traffic_light or "", "unknown")

    def _requires_human_review(self, pre_analysis: PreAnalysis) -> bool:
        if pre_analysis.status == "requires_review":
            return True
        if pre_analysis.confidence is None:
            return True
        return _confidence_or_none(pre_analysis.confidence) < LOW_CONFIDENCE_THRESHOLD

    def _record_conversion_event(
        self,
        *,
        event_name: str,
        source: str,
        case_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        metadata: dict[str, Any] | None,
    ):
        return self.paywalls.create_conversion_event(
            event_name=event_name,
            source=source,
            case_id=case_id,
            user_id=user_id,
            metadata=_json_safe(_safe_metadata(metadata or {})),
        )

    def _record_history_event(
        self,
        *,
        case: LaboraCase,
        actor: User,
        event_type: str,
        title: str,
        description: str | None,
        severity: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.cases.create_history_event(
            case_id=case.id,
            event_type=event_type,
            title=title,
            description=description,
            visibility="both",
            severity=severity,
            created_by_user_id=actor.id,
            metadata=_json_safe(metadata) if metadata else None,
        )

    def _transition_case(
        self,
        case: LaboraCase,
        *,
        new_status: str,
        actor: User | None,
        reason: str,
    ) -> None:
        if case.status == new_status or case.status in LOCKED_CASE_STATUSES:
            return
        validate_case_transition(
            self.db,
            case,
            new_status=new_status,
            validate_transition=True,
        )
        previous_status = case.status
        current_step, next_best_action = step_for_status(new_status)
        case.status = new_status
        case.status_reason = reason
        case.current_step = current_step
        case.next_best_action = next_best_action
        case.updated_at = utc_now()
        self.cases.create_status_history(
            case_id=case.id,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            changed_by_user_id=actor.id if actor else None,
            changed_by_role=self._actor_role(actor),
            source_module="paywall",
            metadata=None,
        )

    def _audit(
        self,
        event_type: str,
        *,
        actor: User | None,
        case: LaboraCase,
        preview: PreviewResult | None,
        ip_address: str | None,
        user_agent: str | None,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        audit_metadata = {
            "caseId": str(case.id),
            "caseNumber": case.case_number,
            "previewId": str(preview.id) if preview else None,
            "actorRole": self._actor_role(actor),
            "sourceModule": "paywall",
        }
        if metadata:
            audit_metadata.update(metadata)
        self.audit_events.create(
            event_type=event_type,
            entity_type="preview_result",
            entity_id=preview.id if preview else None,
            actor_user_id=actor.id if actor else None,
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
            "vista_previa_resultado_paywall.access_denied",
            actor=actor,
            case=case,
            preview=None,
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="UNAUTHORIZED_CASE_ACCESS",
            message="No tienes permisos para acceder a este expediente.",
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
        return (
            self.cases.get_owner(
                case_id=case.id,
                user_id=user.id,
                roles={"authorized_user", "creator", "owner"},
            )
            is not None
        )

    def _can_update(self, case: LaboraCase, user: User) -> bool:
        if user.role in ADMIN_ROLES:
            return True
        if case.owner_user_id == user.id:
            return True
        owner = self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        )
        return owner is not None and owner.permissions.get("edit_case") is True

    def _require_admin_role(self, user: User) -> None:
        if user.role not in PREVIEW_ADMIN_ROLES:
            raise ApiError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="PERMISSION_DENIED",
                message="No tienes permisos para administrar previews de paywall.",
            )

    def _require_case_not_blocked(self, case: LaboraCase) -> None:
        if case.status in LOCKED_CASE_STATUSES or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_423_LOCKED,
                code="PAYWALL_BLOCKED",
                message="El expediente esta bloqueado para vista previa.",
                details={"caseId": str(case.id), "status": case.status},
            )

    def _require_consents(self, user_id: uuid.UUID) -> None:
        permission = ConsentComplianceService(self.db).can_upload_documents(user_id)
        if not permission.allowed:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="CONSENT_REQUIRED",
                message="Debes aceptar los consentimientos requeridos para ver la vista previa.",
                details={
                    "missingConsentTypes": permission.missing_consent_types,
                    "reason": permission.reason,
                },
            )

    def _require_ready_preanalysis(self, case: LaboraCase) -> PreAnalysis:
        pre_analysis = self.pre_analysis.latest_for_case(case.id)
        if pre_analysis is None:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PREANALYSIS_REQUIRED",
                message="Falta el analisis preliminar requerido para preparar la vista previa.",
                details={"caseId": str(case.id)},
            )
        if pre_analysis.status not in READY_PREANALYSIS_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="PREANALYSIS_REQUIRED",
                message="El analisis preliminar aun no esta listo para paywall.",
                details={"caseId": str(case.id), "status": pre_analysis.status},
            )
        return pre_analysis

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_preview_or_404(self, preview_id: str | uuid.UUID) -> PreviewResult:
        preview = self.paywalls.get_preview(preview_id)
        if preview is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="PREVIEW_NOT_AVAILABLE",
                message="Vista previa no encontrada.",
            )
        return preview

    def _is_unlocked(self, case: LaboraCase, paywall: Paywall | None) -> bool:
        return self._case_is_unlocked(case) or (
            paywall is not None
            and (
                paywall.status == "completed"
                or paywall.unlock_required is False
                or paywall.unlocked_at is not None
            )
        )

    def _case_is_unlocked(self, case: LaboraCase) -> bool:
        return case.status in UNLOCKED_CASE_STATUSES

    def _preview_state(self, preview: PreviewResult) -> dict[str, Any]:
        return {
            "id": str(preview.id),
            "caseId": str(preview.case_id),
            "status": preview.status,
            "alertLevel": preview.alert_level,
            "completionScore": preview.completion_score,
            "confidenceScore": preview.confidence_score,
            "requiresHumanReview": preview.requires_human_review,
            "sourcePreanalysisId": str(preview.source_preanalysis_id)
            if preview.source_preanalysis_id
            else None,
        }

    def _admin_item(self, preview: PreviewResult) -> dict[str, Any]:
        return {
            "id": str(preview.id),
            "caseId": str(preview.case_id),
            "userId": str(preview.user_id),
            "status": preview.status,
            "alertLevel": preview.alert_level,
            "completionScore": _float(preview.completion_score),
            "confidenceScore": _float(preview.confidence_score),
            "requiresHumanReview": preview.requires_human_review,
            "createdAt": preview.created_at,
            "updatedAt": preview.updated_at,
        }

    def _actor_role(self, user: User | None) -> str:
        if user is None:
            return "system"
        if user.role in ADMIN_ROLES:
            return "admin"
        if user.role in LEGAL_REVIEWER_ROLES:
            return "legal_reviewer"
        if user.role == "system":
            return "system"
        return "user"


def _safe_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if any(pattern.search(text) for pattern in PROHIBITED_TEXT_PATTERNS):
        return "Detalle reservado para el analisis completo."
    return text[:limit]


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        safe_key = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(key))[:80]
        if not safe_key:
            continue
        safe[safe_key] = _safe_metadata_value(item)
    return safe


def _safe_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _safe_metadata(value)
    if isinstance(value, list):
        return [_safe_metadata_value(item) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return _safe_text(value, limit=500)
        return value
    return str(value)[:200]


def _checkout_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "checkout_version",
        "name",
        "currency",
        "amount",
        "description",
        "lang",
        "country",
        "invoice",
        "response",
        "confirmation",
        "method",
        "uniqueTransactionPerBill",
        "extras",
    }
    return {
        key: _json_safe(value)
        for key, value in payload.items()
        if key in allowed_keys
    }


def _epayco_public_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "x_ref_payco": "refPayco",
        "x_transaction_id": "transactionId",
        "x_cod_response": "responseCode",
        "x_response": "response",
        "x_response_reason_text": "responseReason",
        "x_id_invoice": "invoice",
        "x_amount": "amount",
        "x_amount_ok": "amount",
        "x_currency_code": "currency",
    }
    metadata: dict[str, Any] = {}
    for source_key, target_key in keys.items():
        if source_key in payload and target_key not in metadata:
            metadata[target_key] = _safe_metadata_value(payload[source_key])
    return metadata


def _payment_uuid(payload: dict[str, Any]) -> uuid.UUID | None:
    reference = (
        payload.get("x_ref_payco")
        or payload.get("x_transaction_id")
        or payload.get("x_id_invoice")
    )
    if not reference:
        return None
    return uuid.uuid5(uuid.NAMESPACE_URL, f"epayco:{reference}")


def _stable_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _completion_score_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    if parsed < 0:
        parsed = Decimal("0")
    if parsed > 100:
        parsed = Decimal("100")
    return parsed.quantize(Decimal("0.01"))


def _confidence_or_none(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.0000")
    parsed = Decimal(str(value))
    if parsed < 0:
        parsed = Decimal("0")
    if parsed > 1:
        parsed = Decimal("1")
    return parsed.quantize(Decimal("0.0001"))


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
