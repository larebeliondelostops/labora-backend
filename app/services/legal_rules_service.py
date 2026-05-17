from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable


RULE_ENGINE_VERSION = "legal-rules-base-v1"
RULE_VERSION = "1.0.0"


@dataclass(frozen=True)
class RuleDefinition:
    code: str
    name: str
    category: str
    condition_expression: str
    evaluator: Callable[[dict[str, Any]], dict[str, Any]]


class LegalRulesService:
    """Deterministic, traceable legal rule engine for the full analysis module."""

    def __init__(self, rules: list[RuleDefinition] | None = None) -> None:
        self.rules = rules or _default_rules()

    def evaluate(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "ruleCode": rule.code,
                "ruleName": rule.name,
                "ruleVersion": RULE_VERSION,
                "ruleCategory": rule.category,
                "conditionExpression": rule.condition_expression,
                **rule.evaluator(snapshot),
            }
            for rule in self.rules
        ]


def _default_rules() -> list[RuleDefinition]:
    return [
        RuleDefinition(
            code="REGIME_CLASSIFICATION_001",
            name="Clasificacion inicial de regimen probable",
            category="regime_classification",
            condition_expression="profile and extraction signals indicate public, teacher, special, or general regime",
            evaluator=_regime_classification_rule,
        ),
        RuleDefinition(
            code="CRITICAL_DATA_COMPLETENESS_001",
            name="Datos criticos para analisis completo",
            category="evidence",
            condition_expression="holder birth date, documents, periods, and extraction confidence are present",
            evaluator=_critical_data_rule,
        ),
        RuleDefinition(
            code="PERIOD_WEEK_CONSISTENCY_001",
            name="Consistencia entre periodos y semanas",
            category="missing_weeks",
            condition_expression="difference between reported weeks and calendar-derived weeks is not material",
            evaluator=_period_week_consistency_rule,
        ),
        RuleDefinition(
            code="MISSING_WEEKS_OR_MORA_001",
            name="Posible mora o semanas no reconocidas",
            category="employer_default",
            condition_expression="contribution gaps or missing period claims exist",
            evaluator=_missing_weeks_rule,
        ),
        RuleDefinition(
            code="RELIQUIDATION_SIGNAL_001",
            name="Senales de reliquidacion",
            category="reliquidation",
            condition_expression="salary bases, pensioned status, or questionnaire signal suggest reliquidation",
            evaluator=_reliquidation_rule,
        ),
        RuleDefinition(
            code="SPECIAL_REGIME_SIGNAL_001",
            name="Senales de regimen especial, docente o servidor publico",
            category="special_regime",
            condition_expression="public, teacher, or special regime signals are present",
            evaluator=_special_regime_rule,
        ),
        RuleDefinition(
            code="PROCEDURAL_ROUTE_001",
            name="Ruta procedimental recomendada",
            category="procedural_route",
            condition_expression="detected issues and completeness produce a recommended route",
            evaluator=_procedural_route_rule,
        ),
    ]


def _regime_classification_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    signals = set(snapshot.get("signals") or [])
    periods = snapshot.get("periods") or []
    employers = snapshot.get("employers") or []
    profile = snapshot.get("profile") or {}
    if profile.get("hasTeacherHistory") or "docente_magisterio_posible" in signals:
        probable = "teacher_magisterio"
    elif profile.get("hasSpecialRegimeSignal") or "regimen_especial_posible" in signals:
        probable = "special_regime"
    elif profile.get("hasPublicSectorWork") or "servidor_publico_posible" in signals:
        probable = "public_servant"
    elif any(period.get("regimeHint") for period in periods):
        probable = "mixed_or_reported"
    else:
        probable = "general_pension"
    source_refs = _source_refs_for_periods(periods[:3]) + _source_refs_for_employers(employers[:3])
    return {
        "inputFacts": {
            "signals": sorted(signals),
            "profile": {
                "hasPublicSectorWork": profile.get("hasPublicSectorWork"),
                "hasTeacherHistory": profile.get("hasTeacherHistory"),
                "hasSpecialRegimeSignal": profile.get("hasSpecialRegimeSignal"),
            },
        },
        "sourceRefs": source_refs,
        "result": "passed" if probable == "general_pension" else "warning",
        "resultDetail": {"probableRegime": probable},
        "explanation": f"El regimen probable se clasifica como {probable} con base en los datos estructurados.",
        "confidence": _confidence(88 if source_refs else 72),
        "requiresReview": probable in {"teacher_magisterio", "special_regime", "public_servant"},
    }


def _critical_data_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    case = snapshot.get("case") or {}
    documents = snapshot.get("documents") or []
    periods = snapshot.get("periods") or []
    contribution_weeks = snapshot.get("contributionWeeks") or []
    extraction = snapshot.get("extraction") or {}
    missing = []
    if not case.get("holderBirthDate"):
        missing.append("holder_birth_date")
    if not documents:
        missing.append("documents")
    if not periods and not contribution_weeks:
        missing.append("labor_periods_or_contribution_weeks")
    if not extraction.get("id"):
        missing.append("extraction_run")
    confidence_avg = _decimal(extraction.get("confidenceAvg"), default=Decimal("0.70"))
    if confidence_avg < Decimal("0.60"):
        missing.append("high_confidence_extraction")
    result = "passed" if not missing else "warning"
    return {
        "inputFacts": {
            "documentsCount": len(documents),
            "periodsCount": len(periods),
            "contributionWeeksCount": len(contribution_weeks),
            "extractionStatus": extraction.get("status"),
            "extractionConfirmationStatus": extraction.get("confirmationStatus"),
            "missing": missing,
        },
        "sourceRefs": _source_refs_for_documents(documents[:3]),
        "result": result,
        "resultDetail": {"missingCriticalData": missing},
        "explanation": (
            "Los datos minimos estan disponibles para ejecutar el analisis completo."
            if not missing
            else "Faltan datos criticos o su confianza es baja; el resultado debe tratarse con cautela."
        ),
        "confidence": _confidence(90 if not missing else 62),
        "requiresReview": bool(missing),
    }


def _period_week_consistency_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    periods = snapshot.get("periods") or []
    contribution_weeks = snapshot.get("contributionWeeks") or []
    reported = sum((_decimal(period.get("weeksDetected")) for period in periods), Decimal("0"))
    derived = sum((_decimal(period.get("calendarWeeks")) for period in periods), Decimal("0"))
    contributed = sum((_decimal(item.get("weeks")) for item in contribution_weeks), Decimal("0"))
    comparison_base = reported if reported > 0 else contributed
    difference = abs(derived - comparison_base) if periods and comparison_base else Decimal("0")
    material = bool(periods) and comparison_base > 0 and difference > Decimal("4.00")
    return {
        "inputFacts": {
            "periodsCount": len(periods),
            "contributionWeeksCount": len(contribution_weeks),
            "reportedWeeks": float(reported),
            "contributionWeeks": float(contributed),
            "calendarWeeks": float(derived),
            "differenceWeeks": float(difference),
        },
        "sourceRefs": _source_refs_for_periods(periods[:5]) + _source_refs_for_contribution_weeks(contribution_weeks[:5]),
        "result": "warning" if material else "passed",
        "resultDetail": {"differenceWeeks": float(difference), "materialDifference": material},
        "explanation": (
            "Hay diferencia material entre semanas reportadas y semanas derivadas de periodos."
            if material
            else "No se observa una diferencia material entre periodos y semanas consolidadas."
        ),
        "confidence": _confidence(82 if periods or contribution_weeks else 55),
        "requiresReview": material,
    }


def _missing_weeks_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    gaps = snapshot.get("gaps") or []
    profile = snapshot.get("profile") or {}
    has_claim = bool(profile.get("hasMissingWeeksClaim")) or bool(gaps)
    severity = "high" if len(gaps) >= 2 else "medium" if gaps else "low"
    return {
        "inputFacts": {
            "gapsCount": len(gaps),
            "hasMissingWeeksClaim": profile.get("hasMissingWeeksClaim"),
            "severity": severity,
        },
        "sourceRefs": _source_refs_for_gaps(gaps[:5]),
        "result": "warning" if has_claim else "not_applicable",
        "resultDetail": {"gapsCount": len(gaps), "severity": severity},
        "explanation": (
            "Se detectan vacios o una reclamacion de semanas que pueden soportar revision por mora o no reconocimiento."
            if has_claim
            else "No hay senales estructuradas de mora patronal o semanas no reconocidas."
        ),
        "confidence": _confidence(84 if gaps else 76),
        "requiresReview": bool(gaps),
    }


def _reliquidation_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    salary_bases = snapshot.get("salaryBases") or []
    profile = snapshot.get("profile") or {}
    case = snapshot.get("case") or {}
    situation = str(case.get("situationType") or "")
    has_signal = (
        bool(profile.get("hasReliquidationSignal"))
        or situation in {"pensioned_with_doubts", "recognized_with_possible_error", "reliquidation_needed"}
        or len(salary_bases) >= 3
    )
    return {
        "inputFacts": {
            "salaryBasesCount": len(salary_bases),
            "hasReliquidationSignal": profile.get("hasReliquidationSignal"),
            "situationType": situation,
        },
        "sourceRefs": _source_refs_for_salary_bases(salary_bases[:5]),
        "result": "warning" if has_signal else "not_applicable",
        "resultDetail": {"hasReliquidationSignal": has_signal},
        "explanation": (
            "Existen senales para comparar monto reconocido frente a una estimacion calculada."
            if has_signal
            else "Los datos actuales no muestran una senal fuerte de reliquidacion."
        ),
        "confidence": _confidence(80 if salary_bases else 66),
        "requiresReview": has_signal and not salary_bases,
    }


def _special_regime_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    signals = set(snapshot.get("signals") or [])
    has_signal = bool(signals & {"servidor_publico_posible", "docente_magisterio_posible", "regimen_especial_posible"})
    return {
        "inputFacts": {"signals": sorted(signals)},
        "sourceRefs": _source_refs_for_periods((snapshot.get("periods") or [])[:3]),
        "result": "warning" if has_signal else "not_applicable",
        "resultDetail": {"specialSignals": sorted(signals)},
        "explanation": (
            "Hay senales de regimen especial o tiempos publicos que requieren validacion juridica especifica."
            if has_signal
            else "No se detectan senales estructuradas de regimen especial."
        ),
        "confidence": _confidence(78 if has_signal else 74),
        "requiresReview": has_signal,
    }


def _procedural_route_rule(snapshot: dict[str, Any]) -> dict[str, Any]:
    gaps = snapshot.get("gaps") or []
    profile = snapshot.get("profile") or {}
    salary_bases = snapshot.get("salaryBases") or []
    if not snapshot.get("periods") and not snapshot.get("contributionWeeks"):
        route = "request_documents"
        result = "inconclusive"
    elif gaps or profile.get("hasMissingWeeksClaim"):
        route = "administrative_claim"
        result = "warning"
    elif profile.get("hasReliquidationSignal") or len(salary_bases) >= 3:
        route = "reliquidation_review"
        result = "warning"
    else:
        route = "no_relevant_error"
        result = "passed"
    return {
        "inputFacts": {
            "gapsCount": len(gaps),
            "hasMissingWeeksClaim": profile.get("hasMissingWeeksClaim"),
            "salaryBasesCount": len(salary_bases),
        },
        "sourceRefs": [],
        "result": result,
        "resultDetail": {"recommendedRoute": route},
        "explanation": f"La ruta recomendada por reglas deterministicas es {route}.",
        "confidence": _confidence(82 if result != "inconclusive" else 58),
        "requiresReview": result == "inconclusive",
    }


def _source_refs_for_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "document", "id": item.get("id"), "label": item.get("displayName") or item.get("type")}
        for item in items
        if item.get("id")
    ]


def _source_refs_for_periods(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "labor_period",
            "id": item.get("id"),
            "label": f"{item.get('startDate')} - {item.get('endDate') or 'actual'}",
        }
        for item in items
        if item.get("id")
    ]


def _source_refs_for_employers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "employer", "id": item.get("id"), "label": item.get("name")}
        for item in items
        if item.get("id")
    ]


def _source_refs_for_gaps(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "contribution_gap", "id": item.get("id"), "label": item.get("description") or item.get("gapType")}
        for item in items
        if item.get("id")
    ]


def _source_refs_for_contribution_weeks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "contribution_week",
            "id": item.get("id"),
            "label": f"{item.get('year')}-{item.get('month')}",
        }
        for item in items
        if item.get("id")
    ]


def _source_refs_for_salary_bases(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "salary_base",
            "id": item.get("id"),
            "label": f"{item.get('periodYear')}-{item.get('periodMonth') or ''}",
        }
        for item in items
        if item.get("id")
    ]


def _confidence(value: int | float | Decimal) -> Decimal:
    parsed = Decimal(str(value))
    if parsed < 0:
        parsed = Decimal("0")
    if parsed > 100:
        parsed = Decimal("100")
    return parsed.quantize(Decimal("0.01"))


def _decimal(value: Any, *, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default
