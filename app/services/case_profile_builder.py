from datetime import date
from decimal import Decimal
from typing import Any

from app.models.case import LaboraCase


class CaseProfileBuilder:
    def build(
        self,
        *,
        case: LaboraCase,
        answers: dict[str, Any],
        completion_percentage: float,
    ) -> dict[str, Any]:
        birth_date = _parse_date(answers.get("birth_date")) or case.holder_birth_date
        pension_fund = _text(answers.get("pension_fund")) or case.pension_fund_or_entity
        current_status = _text(answers.get("current_pension_status")) or case.situation_type

        has_public_sector_work = _bool(answers.get("has_public_sector_work"))
        has_teacher_history = _bool(answers.get("has_teacher_history"))
        has_missing_weeks_claim = _bool(answers.get("believes_missing_weeks"))
        has_reliquidation_signal = _bool(answers.get("believes_wrong_allowance"))
        has_prior_claim = _bool(answers.get("has_prior_claim"))
        has_special_regime_signal = has_public_sector_work or has_teacher_history

        critical_facts: list[dict[str, Any]] = []
        missing_documents: list[dict[str, Any]] = []

        if has_public_sector_work:
            critical_facts.append(
                {
                    "code": "public_sector_work",
                    "label": "Tiempo laborado en entidades publicas",
                    "severity": "warning",
                    "source": "questionnaire",
                }
            )
            if not _bool(answers.get("has_public_service_certificates")):
                missing_documents.append(
                    {
                        "code": "public_service_certificate",
                        "label": "Certificacion de tiempo de servicio publico",
                        "reason": "Necesaria para validar tiempos publicos o regimen especial.",
                        "severity": "warning",
                    }
                )

        if has_teacher_history:
            critical_facts.append(
                {
                    "code": "teacher_history",
                    "label": "Posible vinculacion docente o magisterio",
                    "severity": "warning",
                    "source": "questionnaire",
                }
            )
            if not _bool(answers.get("has_teacher_appointment_docs")):
                missing_documents.append(
                    {
                        "code": "teacher_appointment_documents",
                        "label": "Actos de nombramiento, posesion o certificados docentes",
                        "reason": "Ayudan a validar vinculacion docente y regimen aplicable.",
                        "severity": "warning",
                    }
                )

        if has_missing_weeks_claim:
            critical_facts.append(
                {
                    "code": "missing_weeks_claim",
                    "label": "Posible faltante de semanas o periodos",
                    "severity": "warning",
                    "source": "questionnaire",
                    "description": _text(answers.get("missing_weeks_description")),
                }
            )
            if not _bool(answers.get("has_supporting_documents")):
                missing_documents.append(
                    {
                        "code": "missing_weeks_supports",
                        "label": "Soportes de periodos faltantes",
                        "reason": "Se requieren soportes laborales para contrastar semanas.",
                        "severity": "warning",
                    }
                )

        if has_reliquidation_signal:
            critical_facts.append(
                {
                    "code": "reliquidation_signal",
                    "label": "Posible error en liquidacion o factores salariales",
                    "severity": "warning",
                    "source": "questionnaire",
                    "description": _text(answers.get("wrong_allowance_reason")),
                }
            )
            if not _bool(answers.get("has_salary_supports")):
                missing_documents.append(
                    {
                        "code": "salary_supports",
                        "label": "Soportes salariales o desprendibles de nomina",
                        "reason": "Necesarios para revisar factores de liquidacion.",
                        "severity": "warning",
                    }
                )

        detected_route = _detected_route(
            has_reliquidation_signal=has_reliquidation_signal,
            has_missing_weeks_claim=has_missing_weeks_claim,
            has_public_sector_work=has_public_sector_work,
            has_teacher_history=has_teacher_history,
            fallback=case.case_type_requested,
        )
        requires_review = bool(has_special_regime_signal or (critical_facts and missing_documents))
        confidence = _confidence(
            completion_percentage=completion_percentage,
            critical_facts_count=len(critical_facts),
        )

        return {
            "birth_date": birth_date,
            "gender": _text(answers.get("gender")),
            "pension_fund": pension_fund,
            "current_status": current_status,
            "has_public_sector_work": has_public_sector_work,
            "has_teacher_history": has_teacher_history,
            "has_special_regime_signal": has_special_regime_signal,
            "has_missing_weeks_claim": has_missing_weeks_claim,
            "has_reliquidation_signal": has_reliquidation_signal,
            "has_prior_claim": has_prior_claim,
            "detected_route": detected_route,
            "critical_facts": critical_facts,
            "missing_documents": missing_documents,
            "confidence": confidence,
            "requires_review": requires_review,
        }


def _detected_route(
    *,
    has_reliquidation_signal: bool,
    has_missing_weeks_claim: bool,
    has_public_sector_work: bool,
    has_teacher_history: bool,
    fallback: str,
) -> str:
    if has_teacher_history:
        return "teacher_magisterio_case"
    if has_public_sector_work:
        return "public_service_time_review"
    if has_reliquidation_signal:
        return "pension_reliquidation"
    if has_missing_weeks_claim:
        return "missing_weeks_review"
    return fallback or "labor_history_analysis"


def _confidence(*, completion_percentage: float, critical_facts_count: int) -> Decimal:
    base = min(max(completion_percentage / 100, 0.2), 0.95)
    if critical_facts_count:
        base = min(base + 0.05, 0.95)
    return Decimal(str(round(base, 4)))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = " ".join(value.strip().split())
        return stripped or None
    return str(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "si", "s"}
    return bool(value)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
