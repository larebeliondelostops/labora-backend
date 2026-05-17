from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


CALCULATION_VERSION = "1.0.0"


class CalculationService:
    """Reproducible calculation engine for full analysis.

    The first iteration keeps formulas intentionally conservative: it stores
    all inputs, formula references and units without pretending to be a final
    actuarial liquidation.
    """

    def calculate(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        periods = snapshot.get("periods") or []
        contribution_weeks = snapshot.get("contributionWeeks") or []
        gaps = snapshot.get("gaps") or []
        salary_bases = snapshot.get("salaryBases") or []
        calculations = [
            self._weeks_total(periods, contribution_weeks),
            self._weeks_by_period(periods, contribution_weeks),
            self._missing_weeks_estimate(gaps),
            self._salary_base_summary(salary_bases),
        ]
        recognized = self._recognized_amount(snapshot)
        if recognized is not None:
            calculations.append(recognized)
        correct = self._correct_estimated_amount(salary_bases)
        if correct is not None:
            calculations.append(correct)
        difference = self._economic_difference(recognized, correct)
        if difference is not None:
            calculations.append(difference)
            calculations.append(self._retroactive_estimate(difference))
        return calculations

    def _weeks_total(
        self,
        periods: list[dict[str, Any]],
        contribution_weeks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        reported = sum((_decimal(period.get("weeksDetected")) for period in periods), Decimal("0"))
        derived = sum((_decimal(period.get("calendarWeeks")) for period in periods), Decimal("0"))
        contributed = sum((_decimal(item.get("weeks")) for item in contribution_weeks), Decimal("0"))
        if reported > 0:
            result = reported
            used_source = "reported_periods"
        elif contributed > 0:
            result = contributed
            used_source = "contribution_weeks"
        else:
            result = derived
            used_source = "calendar_periods"
        return _calculation(
            code="WEEKS_TOTAL_001",
            name="Total de semanas consolidadas",
            calculation_type="weeks",
            input_values={
                "periodsCount": len(periods),
                "contributionWeeksCount": len(contribution_weeks),
                "reportedWeeks": _number(reported),
                "contributionWeeks": _number(contributed),
                "calendarWeeks": _number(derived),
            },
            formula_ref="sum(period.weeks_detected) fallback sum(contribution_week.weeks) fallback sum(calendar_weeks)",
            formula_expression="reportedWeeks if reportedWeeks > 0 else contributionWeeks if contributionWeeks > 0 else calendarWeeks",
            source_refs=_period_refs(periods) + _contribution_week_refs(contribution_weeks),
            result_value=result,
            result_unit="weeks",
            result_detail={"usedSource": used_source},
            confidence=Decimal("88.00") if periods or contribution_weeks else Decimal("45.00"),
        )

    def _weeks_by_period(
        self,
        periods: list[dict[str, Any]],
        contribution_weeks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if periods:
            detail = [
                {
                    "periodId": period.get("id"),
                    "startDate": period.get("startDate"),
                    "endDate": period.get("endDate"),
                    "weeks": _number(_decimal(period.get("weeksDetected")) or _decimal(period.get("calendarWeeks"))),
                }
                for period in periods
            ]
            source_refs = _period_refs(periods)
            formula_ref = "per-period-weeks-v1"
            formula_expression = "weeks_detected or days_between(start_date, end_date) / 7"
        else:
            detail = [
                {
                    "contributionWeekId": item.get("id"),
                    "year": item.get("year"),
                    "month": item.get("month"),
                    "weeks": _number(_decimal(item.get("weeks"))),
                }
                for item in contribution_weeks
            ]
            source_refs = _contribution_week_refs(contribution_weeks)
            formula_ref = "per-contribution-week-v1"
            formula_expression = "sum(contribution_week.weeks) grouped by year and month"
        total = sum((_decimal(item["weeks"]) for item in detail), Decimal("0"))
        return _calculation(
            code="WEEKS_BY_PERIOD_001",
            name="Semanas por periodo",
            calculation_type="weeks",
            input_values={"periods": detail},
            formula_ref=formula_ref,
            formula_expression=formula_expression,
            source_refs=source_refs,
            result_value=total,
            result_unit="weeks",
            result_detail={"periods": detail},
            confidence=Decimal("86.00") if detail else Decimal("45.00"),
        )

    def _missing_weeks_estimate(self, gaps: list[dict[str, Any]]) -> dict[str, Any]:
        total = sum((_decimal(gap.get("estimatedWeeks")) for gap in gaps), Decimal("0"))
        return _calculation(
            code="MISSING_WEEKS_ESTIMATE_001",
            name="Estimacion de semanas faltantes",
            calculation_type="period_gap",
            input_values={"gapsCount": len(gaps), "estimatedWeeksByGap": [_number(_decimal(gap.get("estimatedWeeks"))) for gap in gaps]},
            formula_ref="gap-days-to-weeks-v1",
            formula_expression="sum(days_between(gap.start_date, gap.end_date) / 7)",
            source_refs=[
                {"type": "contribution_gap", "id": gap.get("id"), "label": gap.get("gapType")}
                for gap in gaps
                if gap.get("id")
            ],
            result_value=total,
            result_unit="weeks",
            result_detail={"gaps": gaps},
            confidence=Decimal("80.00") if gaps else Decimal("78.00"),
        )

    def _salary_base_summary(self, salary_bases: list[dict[str, Any]]) -> dict[str, Any]:
        amounts = [_decimal(item.get("amount")) for item in salary_bases]
        total = sum(amounts, Decimal("0"))
        average = (total / Decimal(len(amounts))).quantize(Decimal("0.01")) if amounts else Decimal("0")
        return _calculation(
            code="SALARY_BASE_SUMMARY_001",
            name="Resumen de ingreso base detectado",
            calculation_type="salary_factor",
            input_values={"salaryBasesCount": len(salary_bases), "amounts": [_number(item) for item in amounts]},
            formula_ref="average-salary-base-v1",
            formula_expression="sum(salary_base.amount) / count(salary_base)",
            source_refs=[
                {"type": "salary_base", "id": item.get("id"), "label": str(item.get("periodYear"))}
                for item in salary_bases
                if item.get("id")
            ],
            result_value=average,
            result_unit="COP",
            result_detail={"count": len(salary_bases), "average": _number(average)},
            confidence=Decimal("84.00") if salary_bases else Decimal("52.00"),
        )

    def _recognized_amount(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        recognized = _decimal((snapshot.get("preAnalysis") or {}).get("recognizedAmount"))
        if recognized <= 0:
            return None
        return _calculation(
            code="RECOGNIZED_AMOUNT_001",
            name="Monto reconocido por entidad",
            calculation_type="recognized_amount",
            input_values={"recognizedAmount": _number(recognized)},
            formula_ref="recognized-amount-from-structured-source",
            formula_expression="recognizedAmount",
            source_refs=[{"type": "pre_analysis", "id": (snapshot.get("preAnalysis") or {}).get("id"), "label": "Preanalisis"}],
            result_value=recognized,
            result_unit="COP",
            result_detail={},
            confidence=Decimal("70.00"),
        )

    def _correct_estimated_amount(self, salary_bases: list[dict[str, Any]]) -> dict[str, Any] | None:
        amounts = [_decimal(item.get("amount")) for item in salary_bases]
        amounts = [item for item in amounts if item > 0]
        if not amounts:
            return None
        average = sum(amounts, Decimal("0")) / Decimal(len(amounts))
        estimate = (average * Decimal("0.65")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return _calculation(
            code="CORRECT_ESTIMATED_AMOUNT_001",
            name="Mesada estimada segun bases detectadas",
            calculation_type="ibl",
            input_values={"averageSalaryBase": _number(average), "replacementRate": 0.65},
            formula_ref="base-ibl-estimate-v1",
            formula_expression="averageSalaryBase * replacementRate",
            source_refs=[
                {"type": "salary_base", "id": item.get("id"), "label": str(item.get("periodYear"))}
                for item in salary_bases
                if item.get("id")
            ],
            result_value=estimate,
            result_unit="COP",
            result_detail={"method": "conservative_base_estimate"},
            confidence=Decimal("72.00"),
        )

    def _economic_difference(
        self,
        recognized: dict[str, Any] | None,
        correct: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if correct is None:
            return None
        recognized_value = _decimal(recognized.get("resultValue")) if recognized else Decimal("0")
        correct_value = _decimal(correct.get("resultValue"))
        difference = max(correct_value - recognized_value, Decimal("0"))
        return _calculation(
            code="ECONOMIC_DIFFERENCE_001",
            name="Diferencia economica estimada",
            calculation_type="difference",
            input_values={
                "recognizedAmount": _number(recognized_value),
                "correctEstimatedAmount": _number(correct_value),
            },
            formula_ref="economic-difference-v1",
            formula_expression="max(correctEstimatedAmount - recognizedAmount, 0)",
            source_refs=[],
            result_value=difference,
            result_unit="COP",
            result_detail={"recognizedMissing": recognized is None},
            confidence=Decimal("68.00") if recognized is None else Decimal("78.00"),
        )

    def _retroactive_estimate(self, difference: dict[str, Any]) -> dict[str, Any]:
        monthly_difference = _decimal(difference.get("resultValue"))
        months = Decimal("12")
        retroactive = (monthly_difference * months).quantize(Decimal("0.01"))
        return _calculation(
            code="RETROACTIVE_ESTIMATE_001",
            name="Retroactivo estimado",
            calculation_type="retroactive",
            input_values={"monthlyDifference": _number(monthly_difference), "months": int(months)},
            formula_ref="retroactive-estimate-v1",
            formula_expression="monthlyDifference * months",
            source_refs=[],
            result_value=retroactive,
            result_unit="COP",
            result_detail={"monthsAssumption": 12, "isAssumption": True},
            confidence=Decimal("62.00"),
        )


def _calculation(
    *,
    code: str,
    name: str,
    calculation_type: str,
    input_values: dict[str, Any],
    formula_ref: str,
    formula_expression: str,
    source_refs: list[dict[str, Any]],
    result_value: Decimal,
    result_unit: str,
    result_detail: dict[str, Any],
    confidence: Decimal,
) -> dict[str, Any]:
    return {
        "calculationCode": code,
        "calculationName": name,
        "calculationVersion": CALCULATION_VERSION,
        "calculationType": calculation_type,
        "inputValues": input_values,
        "formulaRef": formula_ref,
        "formulaExpression": formula_expression,
        "sourceRefs": source_refs,
        "resultValue": result_value.quantize(Decimal("0.01")),
        "resultUnit": result_unit,
        "resultDetail": result_detail,
        "confidence": confidence.quantize(Decimal("0.01")),
    }


def _period_refs(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "labor_period",
            "id": period.get("id"),
            "label": f"{period.get('startDate')} - {period.get('endDate') or 'actual'}",
        }
        for period in periods
        if period.get("id")
    ]


def _contribution_week_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "contribution_week",
            "id": item.get("id"),
            "label": f"{item.get('year')}-{item.get('month')}",
        }
        for item in items
        if item.get("id")
    ]


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _number(value: Decimal) -> int | float:
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral() else float(value)
