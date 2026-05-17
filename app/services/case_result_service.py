import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from app.core.api_errors import ApiError
from app.models.case import CaseHistoryEvent, LaboraCase
from app.models.full_analysis import AnalysisInconsistency, CalculationResult, FullAnalysis, LegalRuleResult
from app.models.paywall import Paywall
from app.models.user import User
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.case_result_repository import CaseResultRepository
from app.repositories.full_analysis_repository import FullAnalysisRepository
from app.repositories.payment_repository import PaymentRepository
from app.utils.dates import utc_now


ADMIN_ROLES = {"admin", "legal_admin"}
LEGAL_REVIEWER_ROLES = {"legal_reviewer"}
OPERATOR_ROLES = {"operator", "legal_ops", "reviewer"}
INTERNAL_ROLES = {*ADMIN_ROLES, *LEGAL_REVIEWER_ROLES, *OPERATOR_ROLES, "system"}
PAYMENT_REQUIRED_STATUS = 423
READY_ANALYSIS_STATUSES = {"completed", "requires_review"}
VISIBLE_RESULT_STATUSES = {"completed", "approved"}
HIDDEN_RESULT_STATUSES = {"rejected", "error", "blocked", "requires_review", "in_progress"}
LOW_CONFIDENCE_THRESHOLD = Decimal("70.00")
CRITICAL_CONFIDENCE_THRESHOLD = Decimal("50.00")
ECONOMIC_WARNING = (
    "Los valores son estimados y pueden cambiar con documentos adicionales, "
    "validacion juridica, actualizacion normativa o revision humana."
)
RESULT_EVENTS = {
    "created": "resultado_completo.created",
    "updated": "resultado_completo.updated",
    "viewed": "resultado_completo.viewed",
    "submitted": "resultado_completo.submitted",
    "approved": "resultado_completo.approved",
    "rejected": "resultado_completo.rejected",
    "failed": "resultado_completo.failed",
    "generation_started": "resultado_completo.generation_started",
    "generation_completed": "resultado_completo.generation_completed",
    "generation_blocked": "resultado_completo.generation_blocked",
    "requires_review": "resultado_completo.requires_review",
    "visibility_enabled": "resultado_completo.visibility_enabled",
    "visibility_disabled": "resultado_completo.visibility_disabled",
    "regenerated": "resultado_completo.regenerated",
    "access_denied": "resultado_completo.access_denied",
}
UNLOCKED_CASE_STATUSES = {
    "paid_unlocked",
    "full_analysis_unlocked",
    "analysis_in_progress",
    "analysis_completed",
    "result_completed",
    "report_ready",
    "legal_action_ready",
    "completed",
    "requires_review",
    "closed",
}


class CaseResultService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cases = CaseRepository(db)
        self.results = CaseResultRepository(db)
        self.full_analysis = FullAnalysisRepository(db)
        self.payments = PaymentRepository(db)
        self.audit_events = AuditEventRepository(db)

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
        result = self.results.latest_for_case(case.id)
        if result is None:
            raise self._result_not_found(case.id)
        if not self._is_internal(user):
            self._require_visible_to_user(result)
        self._audit(
            RESULT_EVENTS["viewed"],
            actor=user,
            case=case,
            result=result,
            metadata={"surface": "result"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._result_payload(case, result, view_logged=True)

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
        result = self.results.latest_for_case(case.id)
        analysis = self.full_analysis.latest_for_case(case.id)
        blockers = self._status_blockers(case=case, result=result, analysis=analysis)
        return {
            "caseId": str(case.id),
            "status": self._status_value(case=case, result=result, analysis=analysis),
            "progress": self._status_progress(result=result, analysis=analysis),
            "isVisibleToUser": bool(result and result.status in VISIBLE_RESULT_STATUSES and result.is_visible_to_user),
            "blockers": blockers,
        }

    def generate(
        self,
        case_id: str,
        *,
        force_regenerate: bool,
        reason: str,
        source_analysis_id: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_internal(case, user, ip_address, user_agent)
        self._require_unlocked(case)

        approved = self.results.latest_approved_for_case(case.id)
        if approved is not None and not force_regenerate:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="RESULT_ALREADY_EXISTS",
                message="Ya existe un resultado aprobado para este expediente.",
                details={"caseId": str(case.id), "resultId": str(approved.id)},
            )

        analysis = self._analysis_for_generation(case, source_analysis_id)
        self._audit(
            RESULT_EVENTS["generation_started"],
            actor=user,
            case=case,
            result=None,
            metadata={"reason": reason, "analysisId": str(analysis.id)},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        snapshot = self._build_snapshot(case=case, analysis=analysis, actor=user)
        result = self.results.create(**snapshot["result"])
        self.results.replace_children(
            result=result,
            final_viability=snapshot["final_viability"],
            economic_estimate=snapshot["economic_estimate"],
            recommended_route=snapshot["recommended_route"],
            cards=snapshot["cards"],
            inconsistencies=snapshot["inconsistencies"],
        )
        self._record_history_event(
            case=case,
            actor=user,
            event_type=RESULT_EVENTS["created"],
            title="Resultado completo generado",
            description="Se consolido el resultado completo del expediente.",
            severity="warning" if result.requires_human_review else "success",
            metadata={"caseResultId": str(result.id), "analysisId": str(analysis.id), "version": result.version},
        )
        event_name = RESULT_EVENTS["regenerated"] if result.version > 1 else RESULT_EVENTS["created"]
        self._audit(
            event_name,
            actor=user,
            case=case,
            result=result,
            new_status=result.status,
            metadata={
                "reason": reason,
                "version": result.version,
                "analysisId": str(analysis.id),
                "confidenceScore": _float(result.confidence_score),
                "aiUsed": False,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._audit(
            RESULT_EVENTS["requires_review"] if result.requires_human_review else RESULT_EVENTS["visibility_enabled"],
            actor=user,
            case=case,
            result=result,
            new_status=result.status,
            metadata={"isVisibleToUser": result.is_visible_to_user},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {
            "jobId": str(result.id),
            "caseId": str(case.id),
            "resultId": str(result.id),
            "status": result.status,
        }

    def update_result(
        self,
        case_id: str,
        result_id: str,
        *,
        payload: dict[str, Any],
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_internal(case, user, ip_address, user_agent)
        result = self._get_result_for_case(case, result_id)
        previous = self._result_state(result)

        status_value = payload.get("status")
        if status_value is not None and status_value == "not_started":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="RESULT_STATUS_INVALID",
                message="No se puede actualizar un resultado a not_started.",
            )

        if payload.get("headline") is not None:
            result.headline = payload["headline"].strip()
        if payload.get("executive_summary") is not None:
            result.executive_summary = payload["executive_summary"].strip()
        if payload.get("user_explanation") is not None:
            result.user_explanation = payload["user_explanation"].strip()
        if payload.get("legal_disclaimer") is not None:
            result.legal_disclaimer = payload["legal_disclaimer"].strip()
        if payload.get("conclusion") is not None:
            result.conclusion = payload["conclusion"].strip()
        if payload.get("requires_human_review") is not None:
            result.requires_human_review = bool(payload["requires_human_review"])
        if status_value is not None:
            result.status = status_value
        if payload.get("is_visible_to_user") is not None:
            result.is_visible_to_user = bool(payload["is_visible_to_user"])

        if result.status in HIDDEN_RESULT_STATUSES:
            result.is_visible_to_user = False
        if result.is_visible_to_user and result.status not in VISIBLE_RESULT_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="RESULT_VISIBILITY_INVALID",
                message="No se puede publicar un resultado con estado no visible.",
            )
        if result.is_visible_to_user and result.requires_human_review and result.status != "approved":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="RESULT_REQUIRES_REVIEW",
                message="El resultado requiere revision antes de ser publicado.",
            )

        result.updated_by = user.id
        result.updated_at = utc_now()
        self._audit(
            RESULT_EVENTS["updated"],
            actor=user,
            case=case,
            result=result,
            previous_status=previous["status"],
            new_status=result.status,
            previous_state=previous,
            new_state=self._result_state(result),
            metadata={"reviewComment": payload.get("review_comment")},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._result_payload(case, result, view_logged=False)

    def approve_result(
        self,
        case_id: str,
        result_id: str,
        *,
        comment: str | None,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_internal(case, user, ip_address, user_agent)
        self._require_unlocked(case)
        result = self._get_result_for_case(case, result_id)
        if result.status == "error":
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="RESULT_CANNOT_BE_APPROVED",
                message="No se puede aprobar un resultado en error.",
            )
        previous_status = result.status
        result.status = "approved"
        result.is_visible_to_user = True
        result.requires_human_review = False
        result.approved_by = user.id
        result.approved_at = utc_now()
        result.updated_by = user.id
        result.updated_at = result.approved_at
        self._record_history_event(
            case=case,
            actor=user,
            event_type=RESULT_EVENTS["approved"],
            title="Resultado completo aprobado",
            description=comment,
            severity="success",
            metadata={"caseResultId": str(result.id)},
        )
        self._audit(
            RESULT_EVENTS["approved"],
            actor=user,
            case=case,
            result=result,
            previous_status=previous_status,
            new_status=result.status,
            metadata={"comment": comment, "isVisibleToUser": True},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return self._result_payload(case, result, view_logged=False)

    def reject_result(
        self,
        case_id: str,
        result_id: str,
        *,
        reason: str,
        send_to_regeneration: bool,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_internal(case, user, ip_address, user_agent)
        result = self._get_result_for_case(case, result_id)
        previous_status = result.status
        result.status = "rejected"
        result.is_visible_to_user = False
        result.requires_human_review = True
        result.updated_by = user.id
        result.updated_at = utc_now()
        self._record_history_event(
            case=case,
            actor=user,
            event_type=RESULT_EVENTS["rejected"],
            title="Resultado completo rechazado",
            description=reason,
            severity="warning",
            metadata={"caseResultId": str(result.id), "sendToRegeneration": send_to_regeneration},
        )
        self._audit(
            RESULT_EVENTS["rejected"],
            actor=user,
            case=case,
            result=result,
            previous_status=previous_status,
            new_status=result.status,
            metadata={"reason": reason, "sendToRegeneration": send_to_regeneration},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if send_to_regeneration:
            self._audit(
                RESULT_EVENTS["submitted"],
                actor=user,
                case=case,
                result=result,
                new_status=result.status,
                metadata={"reason": "send_to_regeneration"},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        self.db.commit()
        return self._result_payload(case, result, view_logged=False)

    def mark_viewed(
        self,
        case_id: str,
        result_id: str,
        *,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        case = self._get_case_or_404(case_id)
        self._require_can_view(case, user, ip_address, user_agent)
        self._require_unlocked(case)
        result = self._get_result_for_case(case, result_id)
        if not self._is_internal(user):
            self._require_visible_to_user(result)
        self._audit(
            RESULT_EVENTS["viewed"],
            actor=user,
            case=case,
            result=result,
            metadata={"surface": "explicit_viewed"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        return {"stored": True}

    def _build_snapshot(
        self,
        *,
        case: LaboraCase,
        analysis: FullAnalysis,
        actor: User,
    ) -> dict[str, Any]:
        calculations = sorted(analysis.calculation_results, key=lambda item: item.created_at)
        rules = sorted(analysis.rule_results, key=lambda item: item.created_at)
        inconsistencies = sorted(analysis.inconsistencies, key=lambda item: item.created_at)
        confidence = _decimal(analysis.confidence_global)
        missing_documents = self._missing_documents(inconsistencies)
        viability = self._build_final_viability(
            analysis=analysis,
            rules=rules,
            inconsistencies=inconsistencies,
            missing_documents=missing_documents,
        )
        economic = self._build_economic_estimate(calculations)
        route = self._build_recommended_route(
            case=case,
            analysis=analysis,
            missing_documents=missing_documents,
            inconsistencies=inconsistencies,
        )
        result_inconsistencies = self._build_result_inconsistencies(inconsistencies)
        requires_review = (
            analysis.requires_human_review
            or confidence < LOW_CONFIDENCE_THRESHOLD
            or viability["level"] == "incomplete"
        )
        status_value = "requires_review" if requires_review else "completed"
        is_visible = status_value == "completed" and confidence >= CRITICAL_CONFIDENCE_THRESHOLD
        main_inconsistency = result_inconsistencies[0]["title"] if result_inconsistencies else None
        headline = self._headline(viability["level"], main_inconsistency)
        executive_summary = self._executive_summary(analysis, main_inconsistency, viability["label"], route["title"])
        user_explanation = self._user_explanation(viability["label"], route["title"], economic["has_economic_estimate"])
        result_values = {
            "case_id": case.id,
            "analysis_id": analysis.id,
            "version": self.results.next_version(case.id),
            "status": status_value,
            "result_type": case.case_type_requested,
            "headline": headline,
            "executive_summary": executive_summary,
            "main_inconsistency": main_inconsistency,
            "conclusion": analysis.legal_conclusion,
            "user_explanation": user_explanation,
            "legal_disclaimer": "Este resultado es una estimacion tecnico-juridica y no reemplaza asesoria legal personalizada.",
            "confidence_score": confidence if confidence > 0 else None,
            "requires_human_review": requires_review,
            "is_visible_to_user": is_visible,
            "created_by": actor.id,
            "updated_by": actor.id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        cards = self._build_cards(viability=viability, economic=economic, route=route, main_inconsistency=main_inconsistency)
        return {
            "result": result_values,
            "final_viability": viability,
            "economic_estimate": economic,
            "recommended_route": route,
            "cards": cards,
            "inconsistencies": result_inconsistencies,
        }

    def _build_final_viability(
        self,
        *,
        analysis: FullAnalysis,
        rules: list[LegalRuleResult],
        inconsistencies: list[AnalysisInconsistency],
        missing_documents: list[str],
    ) -> dict[str, Any]:
        score = _decimal(analysis.confidence_global)
        level = self._viability_level(analysis.viability_level, score, missing_documents)
        strengths = [
            {"code": rule.rule_code, "title": rule.rule_name}
            for rule in rules
            if rule.result == "passed"
        ][:5]
        weaknesses = [
            {"type": item.inconsistency_type, "title": item.title}
            for item in inconsistencies
        ][:5]
        return {
            "level": level,
            "label": _viability_label(level),
            "score": score if score > 0 else None,
            "color": _viability_color(level),
            "rationale": analysis.legal_conclusion
            or "Resultado consolidado a partir del motor juridico, calculos y trazabilidad documental.",
            "strengths": strengths,
            "weaknesses": weaknesses,
            "missing_information": missing_documents,
        }

    def _build_economic_estimate(self, calculations: list[CalculationResult]) -> dict[str, Any]:
        difference = _calculation_by_code(calculations, "ECONOMIC_DIFFERENCE_001")
        retroactive = _calculation_by_code(calculations, "RETROACTIVE_ESTIMATE_001")
        recognized = _calculation_by_code(calculations, "RECOGNIZED_AMOUNT_001")
        corrected = _calculation_by_code(calculations, "CORRECT_ESTIMATED_AMOUNT_001")
        claimable = _decimal(retroactive.result_value if retroactive else None)
        if claimable <= 0:
            claimable = _decimal(difference.result_value if difference else None)
        monthly_difference = _decimal(difference.result_value if difference else None)
        has_estimate = claimable > 0 or monthly_difference > 0
        warning = ECONOMIC_WARNING if has_estimate else "No hay base suficiente para estimar un valor reclamable con los datos actuales."
        reference = retroactive or difference or corrected or recognized
        min_amount = (claimable * Decimal("0.85")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if has_estimate else None
        max_amount = (claimable * Decimal("1.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if has_estimate else None
        return {
            "currency": "COP",
            "estimated_claimable_amount": claimable if has_estimate else None,
            "estimated_retroactive_amount": _positive_or_none(retroactive.result_value if retroactive else None) if has_estimate else None,
            "estimated_monthly_difference": _positive_or_none(monthly_difference) if has_estimate else None,
            "recognized_scenario_amount": _positive_or_none(recognized.result_value if recognized else None),
            "corrected_scenario_amount": _positive_or_none(corrected.result_value if corrected else None),
            "has_economic_estimate": has_estimate,
            "estimate_type": "conservative_estimate" if has_estimate else "not_available",
            "calculation_reference_id": reference.id if reference else None,
            "confidence_score": reference.confidence if reference else None,
            "min_amount": min_amount,
            "max_amount": max_amount,
            "assumptions": self._economic_assumptions(calculations),
            "warnings": [warning],
        }

    def _build_recommended_route(
        self,
        *,
        case: LaboraCase,
        analysis: FullAnalysis,
        missing_documents: list[str],
        inconsistencies: list[AnalysisInconsistency],
    ) -> dict[str, Any]:
        route_key = _route_key(analysis.recommended_route, inconsistencies, missing_documents)
        route = _route_catalog(route_key)
        required_documents = missing_documents[:5]
        blockers = []
        if required_documents and route["route_type"] not in {"collect_more_documents", "incomplete_case"}:
            blockers.append({"code": "DOCUMENTS_REQUIRED", "message": "Faltan soportes para ejecutar esta ruta sin riesgo."})
        if analysis.requires_human_review:
            blockers.append({"code": "HUMAN_REVIEW_REQUIRED", "message": "El analisis completo requiere revision juridica."})
        return {
            **route,
            "next_action_url": route["next_action_url"].replace("{case_id}", str(case.id)) if route.get("next_action_url") else None,
            "requires_documents": bool(required_documents) or route["requires_documents"],
            "requires_professional_review": bool(analysis.requires_human_review) or route["requires_professional_review"],
            "blockers": blockers,
            "required_documents": required_documents,
            "rationale": analysis.legal_conclusion
            or "Ruta derivada de reglas verificables y calculos estructurados; no se recomienda demanda por defecto.",
        }

    def _build_result_inconsistencies(self, inconsistencies: list[AnalysisInconsistency]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for index, item in enumerate(inconsistencies):
            source_refs = (item.evidence_refs or []) + (item.legal_rule_refs or []) + (item.calculation_refs or [])
            items.append(
                {
                    "inconsistency_type": item.inconsistency_type,
                    "title": item.title,
                    "description": item.description,
                    "evidence_summary": _evidence_summary(item),
                    "legal_impact": _impact_level(item.severity),
                    "economic_impact": "estimated" if item.economic_impact_estimated else "not_estimated",
                    "estimated_amount": item.economic_impact_estimated,
                    "confidence_score": item.confidence,
                    "source_document_ids": _source_document_ids(source_refs),
                    "source_references": _json_safe(source_refs),
                    "required_documents": item.missing_documents or [],
                    "sort_order": index,
                }
            )
        return items

    def _build_cards(
        self,
        *,
        viability: dict[str, Any],
        economic: dict[str, Any],
        route: dict[str, Any],
        main_inconsistency: str | None,
    ) -> list[dict[str, Any]]:
        cards = [
            {
                "card_key": "viability",
                "title": "Viabilidad",
                "value": viability["label"],
                "description": viability["rationale"][:240],
                "icon": "traffic-cone",
                "tone": _tone_for_viability(viability["level"]),
                "sort_order": 10,
                "metadata_json": {"color": viability["color"], "score": _float(viability.get("score"))},
            },
            {
                "card_key": "claimable_amount",
                "title": "Valor estimado reclamable",
                "value": _money_label(economic.get("estimated_claimable_amount")),
                "description": "Estimacion conservadora sujeta a validacion.",
                "icon": "banknote",
                "tone": "info" if economic["has_economic_estimate"] else "neutral",
                "sort_order": 20,
                "metadata_json": {"hasEconomicEstimate": economic["has_economic_estimate"]},
            },
            {
                "card_key": "retroactive",
                "title": "Retroactivo estimado",
                "value": _money_label(economic.get("estimated_retroactive_amount")),
                "description": "No se muestra si no hay base suficiente.",
                "icon": "calendar-clock",
                "tone": "info" if economic["has_economic_estimate"] else "neutral",
                "sort_order": 30,
                "metadata_json": {},
            },
            {
                "card_key": "main_inconsistency",
                "title": "Inconsistencia principal",
                "value": main_inconsistency or "Sin inconsistencia relevante",
                "description": None,
                "icon": "alert-triangle",
                "tone": "warning" if main_inconsistency else "neutral",
                "sort_order": 40,
                "metadata_json": {},
            },
            {
                "card_key": "recommended_route",
                "title": "Ruta recomendada",
                "value": route["title"],
                "description": route["description"],
                "icon": "route",
                "tone": "success" if route["route_type"] not in {"no_action", "low_viability"} else "neutral",
                "sort_order": 50,
                "metadata_json": {"routeType": route["route_type"]},
            },
        ]
        if route.get("required_documents"):
            cards.append(
                {
                    "card_key": "missing_documents",
                    "title": "Documentos faltantes",
                    "value": str(len(route["required_documents"])),
                    "description": "Soportes adicionales que pueden mejorar la certeza del resultado.",
                    "icon": "file-warning",
                    "tone": "warning",
                    "sort_order": 60,
                    "metadata_json": {"documents": route["required_documents"]},
                }
            )
        return cards

    def _result_payload(self, case: LaboraCase, result, *, view_logged: bool) -> dict[str, Any]:
        cards = self.results.list_cards(result.id)
        inconsistencies = self.results.list_inconsistencies(result.id)
        route = result.recommended_route
        estimate = result.economic_estimate
        viability = result.final_viability
        warnings = list(estimate.warnings if estimate else [])
        if result.requires_human_review:
            warnings.append({"code": "HUMAN_REVIEW_REQUIRED", "message": "El resultado requiere revision humana.", "severity": "warning"})
        missing_documents = self._payload_missing_documents(viability, route, inconsistencies)
        blockers = self._payload_blockers(result, route)
        return {
            "caseId": str(case.id),
            "caseCode": case.case_number,
            "resultId": str(result.id),
            "version": result.version,
            "status": result.status,
            "isVisibleToUser": result.is_visible_to_user,
            "generatedAt": result.created_at,
            "approvedAt": result.approved_at,
            "headline": result.headline,
            "executiveSummary": result.executive_summary,
            "userExplanation": result.user_explanation,
            "legalDisclaimer": result.legal_disclaimer,
            "finalViability": self._viability_payload(viability),
            "economicEstimate": self._economic_payload(estimate),
            "mainInconsistency": self._main_inconsistency_payload(result, inconsistencies),
            "cards": [self._card_payload(card) for card in cards],
            "inconsistencies": [self._inconsistency_payload(item) for item in inconsistencies],
            "recommendedRoute": self._route_payload(route),
            "missingDocuments": missing_documents,
            "warnings": _json_safe(warnings),
            "blockers": blockers,
            "availableActions": self._available_actions(case, result, route, blockers),
            "audit": {"viewLogged": view_logged},
        }

    def _status_blockers(
        self,
        *,
        case: LaboraCase,
        result,
        analysis: FullAnalysis | None,
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        if not self._case_is_unlocked(case):
            blockers.append({"code": "PAYMENT_REQUIRED", "message": "El resultado completo se desbloquea despues del pago."})
        if analysis is None:
            blockers.append({"code": "ANALYSIS_NOT_COMPLETED", "message": "El analisis completo aun no esta listo."})
        elif analysis.status not in READY_ANALYSIS_STATUSES:
            blockers.append({"code": "ANALYSIS_NOT_COMPLETED", "message": "El analisis completo aun esta en proceso."})
        if result is None:
            blockers.append({"code": "RESULT_NOT_FOUND", "message": "Aun no hay resultado completo disponible."})
        elif result.requires_human_review and result.status != "approved":
            blockers.append({"code": "HUMAN_REVIEW_REQUIRED", "message": "El resultado requiere revision juridica antes de mostrarse."})
        elif not result.is_visible_to_user and result.status in VISIBLE_RESULT_STATUSES:
            blockers.append({"code": "VISIBILITY_DISABLED", "message": "El resultado aun no esta visible para el usuario."})
        return blockers

    def _status_value(self, *, case: LaboraCase, result, analysis: FullAnalysis | None) -> str:
        if result is not None:
            return result.status
        if not self._case_is_unlocked(case):
            return "blocked"
        if analysis is None:
            return "not_started"
        if analysis.status in {"queued", "in_progress", "rules_running", "calculations_running", "scenario_comparison_running", "confidence_evaluation_running"}:
            return "in_progress"
        if analysis.status in {"blocked", "failed", "cancelled"}:
            return "blocked" if analysis.status == "blocked" else "error"
        return "not_started"

    def _status_progress(self, *, result, analysis: FullAnalysis | None) -> int:
        if result is not None:
            return 100
        if analysis is None:
            return 0
        job = self.full_analysis.latest_job(analysis.id)
        if job is not None:
            return int(min(max(_decimal(job.progress), Decimal("0")), Decimal("100")))
        return {
            "queued": 10,
            "in_progress": 30,
            "rules_running": 45,
            "calculations_running": 60,
            "scenario_comparison_running": 75,
            "confidence_evaluation_running": 90,
            "completed": 100,
            "requires_review": 100,
        }.get(analysis.status, 0)

    def _require_visible_to_user(self, result) -> None:
        if result.status in VISIBLE_RESULT_STATUSES and result.is_visible_to_user:
            return
        if result.status == "requires_review" or result.requires_human_review:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="RESULT_REQUIRES_REVIEW",
                message="El resultado requiere revision antes de ser publicado.",
                details={"resultId": str(result.id)},
            )
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="RESULT_BLOCKED",
            message="El resultado completo aun no esta disponible para el usuario.",
            details={"resultId": str(result.id), "status": result.status},
        )

    def _analysis_for_generation(self, case: LaboraCase, source_analysis_id: str | None) -> FullAnalysis:
        analysis = self.full_analysis.get(source_analysis_id) if source_analysis_id else self.full_analysis.latest_for_case(case.id)
        if analysis is None or analysis.case_id != case.id:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_NOT_COMPLETED",
                message="El analisis completo aun esta en proceso.",
                details={"caseId": str(case.id)},
            )
        if analysis.status not in READY_ANALYSIS_STATUSES:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_NOT_COMPLETED",
                message="El analisis completo aun esta en proceso.",
                details={"caseId": str(case.id), "analysisStatus": analysis.status},
            )
        if not analysis.legal_conclusion and not analysis.executive_result:
            raise ApiError(
                status_code=status.HTTP_409_CONFLICT,
                code="ANALYSIS_NOT_COMPLETED",
                message="El motor juridico aun no genero una conclusion verificable.",
                details={"caseId": str(case.id), "analysisId": str(analysis.id)},
            )
        return analysis

    def _get_case_or_404(self, case_id: str | uuid.UUID) -> LaboraCase:
        case = self.cases.get(case_id)
        if case is None or case.deleted_at is not None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="CASE_NOT_FOUND",
                message="Expediente no encontrado.",
            )
        return case

    def _get_result_for_case(self, case: LaboraCase, result_id: str):
        result = self.results.get(result_id)
        if result is None or result.case_id != case.id or result.deleted_at is not None:
            raise self._result_not_found(case.id)
        return result

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

    def _require_internal(
        self,
        case: LaboraCase,
        user: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        if self._is_internal(user):
            return
        self._audit_access_denied(case=case, actor=user, ip_address=ip_address, user_agent=user_agent)

    def _can_view(self, case: LaboraCase, user: User) -> bool:
        if self._is_internal(user):
            return True
        if case.owner_user_id == user.id:
            return True
        return self.cases.get_owner(
            case_id=case.id,
            user_id=user.id,
            roles={"authorized_user", "creator", "owner"},
        ) is not None

    def _is_internal(self, user: User) -> bool:
        return user.role in INTERNAL_ROLES

    def _require_unlocked(self, case: LaboraCase) -> None:
        if self._case_is_unlocked(case):
            return
        raise ApiError(
            status_code=PAYMENT_REQUIRED_STATUS,
            code="PAYMENT_REQUIRED",
            message="Debes completar el pago para desbloquear el resultado completo.",
            details={"caseId": str(case.id)},
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

    def _audit_access_denied(
        self,
        *,
        case: LaboraCase,
        actor: User,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._audit(
            RESULT_EVENTS["access_denied"],
            actor=actor,
            case=case,
            result=None,
            metadata={"blockedReason": "permission_denied"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.commit()
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN_CASE_ACCESS",
            message="No tienes permiso para acceder a este expediente.",
        )

    def _record_history_event(
        self,
        *,
        case: LaboraCase,
        actor: User | None,
        event_type: str,
        title: str,
        description: str | None,
        severity: str,
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
        result,
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
            "caseResultId": str(result.id) if result else None,
            "actorRole": self._actor_role(actor),
            "previousStatus": previous_status,
            "newStatus": new_status,
            "sourceModule": "case_result",
        }
        if metadata:
            event_metadata.update(metadata)
        self.results.create_audit_event(
            case_id=case.id,
            case_result_id=result.id if result else None,
            actor_id=actor.id if actor else None,
            actor_role=self._actor_role(actor),
            event_type=event_type,
            previous_status=previous_status,
            new_status=new_status,
            metadata=_json_safe(event_metadata),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.audit_events.create(
            event_type=event_type,
            entity_type="case_result",
            entity_id=result.id if result else None,
            actor_user_id=actor.id if actor else None,
            previous_state=_json_safe(previous_state) if previous_state else None,
            new_state=_json_safe(new_state) if new_state else None,
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

    def _result_not_found(self, case_id: uuid.UUID) -> ApiError:
        return ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="RESULT_NOT_FOUND",
            message="Aun no hay resultado completo disponible para este expediente.",
            details={"caseId": str(case_id)},
        )

    def _result_state(self, result) -> dict[str, Any]:
        return {
            "id": str(result.id),
            "status": result.status,
            "headline": result.headline,
            "requiresHumanReview": result.requires_human_review,
            "isVisibleToUser": result.is_visible_to_user,
            "version": result.version,
        }

    def _missing_documents(self, inconsistencies: list[AnalysisInconsistency]) -> list[str]:
        docs: list[str] = []
        for item in inconsistencies:
            for document in item.missing_documents or []:
                if document and document not in docs:
                    docs.append(str(document))
        return docs

    def _payload_missing_documents(self, viability, route, inconsistencies) -> list[dict[str, Any]]:
        values: list[str] = []
        if viability:
            values.extend(str(item) for item in viability.missing_information or [])
        if route:
            values.extend(str(item) for item in route.required_documents or [])
        for item in inconsistencies:
            values.extend(str(document) for document in item.required_documents or [])
        unique = []
        for value in values:
            if value and value not in unique:
                unique.append(value)
        return [{"code": _slug(value), "title": value, "required": True} for value in unique]

    def _payload_blockers(self, result, route) -> list[dict[str, Any]]:
        blockers = list(route.blockers if route else [])
        if result.requires_human_review and result.status != "approved":
            blockers.append({"code": "HUMAN_REVIEW_REQUIRED", "message": "El resultado requiere revision juridica."})
        if result.status in {"rejected", "error"}:
            blockers.append({"code": "RESULT_BLOCKED", "message": "El resultado no esta disponible para el usuario."})
        return _json_safe(blockers)

    def _available_actions(self, case: LaboraCase, result, route, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        visible = result.status in VISIBLE_RESULT_STATUSES and result.is_visible_to_user
        legal_enabled = bool(visible and route and route.can_generate_legal_action and result.status != "rejected")
        return [
            {
                "type": "generate_report",
                "label": "Generar informe",
                "enabled": visible,
                "href": f"/cases/{case.id}/reports" if visible else None,
                "reason": None if visible else "result_not_visible",
            },
            {
                "type": "generate_legal_action",
                "label": "Generar reclamacion",
                "enabled": legal_enabled,
                "href": f"/cases/{case.id}/legal-actions/new" if legal_enabled else None,
                "reason": None if legal_enabled else "route_not_available",
            },
        ]

    def _viability_payload(self, item) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "level": item.level,
            "label": item.label,
            "score": _float(item.score),
            "color": item.color,
            "rationale": item.rationale,
            "strengths": item.strengths or [],
            "weaknesses": item.weaknesses or [],
            "missingInformation": item.missing_information or [],
        }

    def _economic_payload(self, item) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "currency": item.currency,
            "hasEconomicEstimate": item.has_economic_estimate,
            "estimatedClaimableAmount": _float(item.estimated_claimable_amount),
            "estimatedRetroactiveAmount": _float(item.estimated_retroactive_amount),
            "estimatedMonthlyDifference": _float(item.estimated_monthly_difference),
            "recognizedScenarioAmount": _float(item.recognized_scenario_amount),
            "correctedScenarioAmount": _float(item.corrected_scenario_amount),
            "minAmount": _float(item.min_amount),
            "maxAmount": _float(item.max_amount),
            "warnings": item.warnings or [],
            "assumptions": item.assumptions or [],
        }

    def _route_payload(self, item) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "routeType": item.route_type,
            "title": item.title,
            "description": item.description,
            "priority": item.priority,
            "nextActionLabel": item.next_action_label,
            "nextActionType": item.next_action_type,
            "nextActionUrl": item.next_action_url,
            "requiresDocuments": item.requires_documents,
            "requiresProfessionalReview": item.requires_professional_review,
            "canGenerateLegalAction": item.can_generate_legal_action,
            "recommendedLegalActionType": item.recommended_legal_action_type,
            "rationale": item.rationale,
            "blockers": item.blockers or [],
            "requiredDocuments": item.required_documents or [],
        }

    def _card_payload(self, item) -> dict[str, Any]:
        return {
            "key": item.card_key,
            "title": item.title,
            "value": item.value,
            "description": item.description,
            "icon": item.icon,
            "tone": item.tone,
            "metadata": item.metadata_json or {},
        }

    def _inconsistency_payload(self, item) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "type": item.inconsistency_type,
            "title": item.title,
            "description": item.description,
            "evidenceSummary": item.evidence_summary,
            "legalImpact": item.legal_impact,
            "economicImpact": item.economic_impact,
            "estimatedAmount": _float(item.estimated_amount),
            "confidenceScore": _float(item.confidence_score),
            "sourceReferences": item.source_references or [],
            "requiredDocuments": item.required_documents or [],
        }

    def _main_inconsistency_payload(self, result, inconsistencies) -> dict[str, str] | None:
        if inconsistencies:
            first = inconsistencies[0]
            return {"title": first.title, "description": first.description}
        if result.main_inconsistency:
            return {"title": result.main_inconsistency, "description": result.main_inconsistency}
        return None

    def _viability_level(self, raw_level: str | None, score: Decimal, missing_documents: list[str]) -> str:
        normalized = (raw_level or "").strip().lower()
        if normalized not in {"high", "medium", "low", "incomplete", "not_applicable"}:
            normalized = "high" if score >= Decimal("80") else "medium" if score >= Decimal("65") else "low"
        if score < CRITICAL_CONFIDENCE_THRESHOLD:
            return "low"
        if missing_documents and normalized == "high":
            return "medium"
        return normalized

    def _economic_assumptions(self, calculations: list[CalculationResult]) -> list[dict[str, Any]]:
        assumptions = []
        for item in calculations:
            detail = item.result_detail or {}
            if detail.get("isAssumption") or detail.get("recognizedMissing"):
                assumptions.append({"calculationCode": item.calculation_code, "detail": detail})
        return _json_safe(assumptions)

    def _headline(self, viability_level: str, main_inconsistency: str | None) -> str:
        if viability_level == "high":
            return "Encontramos una inconsistencia relevante en tu caso"
        if viability_level == "medium":
            return "Encontramos indicios que conviene revisar con soportes"
        if viability_level == "incomplete":
            return "Faltan documentos para cerrar una conclusion completa"
        if main_inconsistency:
            return "Hay una observacion que debe revisarse con cautela"
        return "No encontramos una inconsistencia relevante con los datos actuales"

    def _executive_summary(self, analysis: FullAnalysis, main_inconsistency: str | None, viability: str, route: str) -> str:
        if analysis.summary and analysis.summary.get("plainLanguageSummary"):
            base = str(analysis.summary["plainLanguageSummary"])
        elif main_inconsistency:
            base = f"El analisis detecto como punto principal: {main_inconsistency}."
        else:
            base = "El analisis consolido reglas juridicas, calculos y trazabilidad documental."
        return f"{base} Viabilidad: {viability}. Ruta sugerida: {route}."

    def _user_explanation(self, viability: str, route: str, has_estimate: bool) -> str:
        estimate_text = (
            "Incluimos una estimacion economica conservadora."
            if has_estimate
            else "No mostramos monto porque los datos no permiten una estimacion responsable."
        )
        return f"En terminos simples, tu caso queda con {viability.lower()} y la ruta recomendada es: {route}. {estimate_text}"


def _route_catalog(route_key: str) -> dict[str, Any]:
    catalog = {
        "no_action": {
            "route_type": "no_action",
            "title": "No hacer nada por ahora",
            "description": "Con los datos actuales no se detecta una accion recomendable.",
            "priority": 5,
            "next_action_label": "Ver informe",
            "next_action_type": "generate_report",
            "next_action_url": "/cases/{case_id}/reports",
            "requires_documents": False,
            "requires_professional_review": False,
            "can_generate_legal_action": False,
            "recommended_legal_action_type": None,
        },
        "collect_more_documents": {
            "route_type": "collect_more_documents",
            "title": "Reunir mas soportes",
            "description": "Antes de reclamar conviene completar documentos o evidencias faltantes.",
            "priority": 1,
            "next_action_label": "Cargar documentos",
            "next_action_type": "upload_documents",
            "next_action_url": "/cases/{case_id}/documents",
            "requires_documents": True,
            "requires_professional_review": False,
            "can_generate_legal_action": False,
            "recommended_legal_action_type": None,
        },
        "administrative_claim": {
            "route_type": "administrative_claim",
            "title": "Presentar reclamacion administrativa",
            "description": "Antes de una demanda, se recomienda solicitar correccion ante la entidad.",
            "priority": 1,
            "next_action_label": "Generar reclamacion",
            "next_action_type": "generate_legal_action",
            "next_action_url": "/cases/{case_id}/legal-actions/new",
            "requires_documents": False,
            "requires_professional_review": False,
            "can_generate_legal_action": True,
            "recommended_legal_action_type": "administrative_claim",
        },
        "reliquidation_request": {
            "route_type": "reliquidation_request",
            "title": "Solicitar reliquidacion",
            "description": "La ruta inicial es pedir revision o reliquidacion ante la entidad.",
            "priority": 1,
            "next_action_label": "Generar solicitud",
            "next_action_type": "generate_legal_action",
            "next_action_url": "/cases/{case_id}/legal-actions/new",
            "requires_documents": False,
            "requires_professional_review": False,
            "can_generate_legal_action": True,
            "recommended_legal_action_type": "reliquidation_request",
        },
        "petition_right": {
            "route_type": "petition_right",
            "title": "Presentar derecho de peticion",
            "description": "Conviene pedir informacion o correccion antes de otra accion.",
            "priority": 2,
            "next_action_label": "Generar derecho de peticion",
            "next_action_type": "generate_legal_action",
            "next_action_url": "/cases/{case_id}/legal-actions/new",
            "requires_documents": False,
            "requires_professional_review": False,
            "can_generate_legal_action": True,
            "recommended_legal_action_type": "petition_right",
        },
        "professional_review": {
            "route_type": "professional_review",
            "title": "Solicitar revision profesional",
            "description": "La incertidumbre del caso amerita revision juridica antes de actuar.",
            "priority": 1,
            "next_action_label": "Solicitar revision",
            "next_action_type": "professional_review",
            "next_action_url": "/cases/{case_id}/review",
            "requires_documents": False,
            "requires_professional_review": True,
            "can_generate_legal_action": False,
            "recommended_legal_action_type": None,
        },
        "legal_claim_draft": {
            "route_type": "legal_claim_draft",
            "title": "Evaluar borrador de demanda",
            "description": "Solo procede despues de validar soportes, requisito previo y riesgo.",
            "priority": 4,
            "next_action_label": "Preparar borrador",
            "next_action_type": "generate_legal_action",
            "next_action_url": "/cases/{case_id}/legal-actions/new",
            "requires_documents": False,
            "requires_professional_review": True,
            "can_generate_legal_action": True,
            "recommended_legal_action_type": "legal_claim_draft",
        },
        "low_viability": {
            "route_type": "low_viability",
            "title": "Baja viabilidad",
            "description": "No se recomienda avanzar sin nuevos soportes o cambio en los datos.",
            "priority": 5,
            "next_action_label": "Ver informe",
            "next_action_type": "generate_report",
            "next_action_url": "/cases/{case_id}/reports",
            "requires_documents": False,
            "requires_professional_review": False,
            "can_generate_legal_action": False,
            "recommended_legal_action_type": None,
        },
    }
    return catalog.get(route_key, catalog["professional_review"]).copy()


def _route_key(raw_route: str | None, inconsistencies: list[AnalysisInconsistency], missing_documents: list[str]) -> str:
    route = (raw_route or "").strip().lower()
    if missing_documents and route in {"request_documents", "collect_more_documents"}:
        return "collect_more_documents"
    if route in {"request_documents", "incomplete_case"}:
        return "collect_more_documents"
    if route in {"administrative_claim", "admin_claim"}:
        return "administrative_claim"
    if route in {"reliquidation_review", "reliquidation_request"}:
        return "reliquidation_request"
    if route in {"petition_right", "derecho_peticion"}:
        return "petition_right"
    if route in {"professional_review", "human_review"}:
        return "professional_review"
    if route in {"legal_claim_draft", "legal_claim", "lawsuit"}:
        return "legal_claim_draft"
    if route in {"no_relevant_error", "no_action"}:
        return "no_action"
    if not inconsistencies:
        return "no_action"
    return "professional_review"


def _calculation_by_code(calculations: list[CalculationResult], code: str) -> CalculationResult | None:
    for item in calculations:
        if item.calculation_code == code:
            return item
    return None


def _source_document_ids(source_refs: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("id"))
        for item in source_refs
        if item.get("type") == "document" and item.get("id")
    ]


def _evidence_summary(item: AnalysisInconsistency) -> str | None:
    refs = item.evidence_refs or []
    if not refs:
        return None
    labels = [str(ref.get("label") or ref.get("type")) for ref in refs[:3]]
    return ", ".join(label for label in labels if label)


def _impact_level(severity: str | None) -> str:
    if severity in {"critical", "high"}:
        return "high"
    if severity == "medium":
        return "medium"
    return "low"


def _viability_label(level: str) -> str:
    return {
        "high": "Viabilidad alta",
        "medium": "Viabilidad media",
        "low": "Viabilidad baja",
        "incomplete": "Informacion incompleta",
        "not_applicable": "No aplica",
    }.get(level, "Viabilidad media")


def _viability_color(level: str) -> str:
    return {
        "high": "green",
        "medium": "yellow",
        "low": "orange",
        "incomplete": "gray",
        "not_applicable": "gray",
    }.get(level, "gray")


def _tone_for_viability(level: str) -> str:
    return {
        "high": "success",
        "medium": "warning",
        "low": "danger",
        "incomplete": "neutral",
        "not_applicable": "neutral",
    }.get(level, "neutral")


def _money_label(value: Any) -> str | None:
    amount = _decimal(value)
    if amount <= 0:
        return None
    integer = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"${integer:,} COP".replace(",", ".")


def _positive_or_none(value: Any) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed > 0 else None


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("/", "_")[:80]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value
