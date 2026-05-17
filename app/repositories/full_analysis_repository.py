import uuid
from typing import Any

from sqlalchemy import asc, desc, func
from sqlalchemy.orm import Session

from app.models.full_analysis import (
    AnalysisInconsistency,
    CalculationResult,
    ConfidenceScore,
    FullAnalysis,
    FullAnalysisJob,
    LegalRuleResult,
    Scenario,
)


ACTIVE_STATUSES = {
    "queued",
    "in_progress",
    "rules_running",
    "calculations_running",
    "scenario_comparison_running",
    "confidence_evaluation_running",
}


class FullAnalysisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, full_analysis_id: str | uuid.UUID) -> FullAnalysis | None:
        parsed = _parse_uuid(full_analysis_id)
        if parsed is None:
            return None
        return self.db.get(FullAnalysis, parsed)

    def latest_for_case(self, case_id: uuid.UUID) -> FullAnalysis | None:
        return (
            self.db.query(FullAnalysis)
            .filter(FullAnalysis.case_id == case_id)
            .order_by(desc(FullAnalysis.version), desc(FullAnalysis.created_at))
            .first()
        )

    def active_for_case(self, case_id: uuid.UUID) -> FullAnalysis | None:
        return (
            self.db.query(FullAnalysis)
            .filter(
                FullAnalysis.case_id == case_id,
                FullAnalysis.status.in_(ACTIVE_STATUSES),
            )
            .order_by(desc(FullAnalysis.created_at))
            .first()
        )

    def latest_completed_for_case(self, case_id: uuid.UUID) -> FullAnalysis | None:
        return (
            self.db.query(FullAnalysis)
            .filter(
                FullAnalysis.case_id == case_id,
                FullAnalysis.status.in_(["completed", "requires_review"]),
            )
            .order_by(desc(FullAnalysis.version), desc(FullAnalysis.created_at))
            .first()
        )

    def next_version(self, case_id: uuid.UUID) -> int:
        current = (
            self.db.query(func.max(FullAnalysis.version))
            .filter(FullAnalysis.case_id == case_id)
            .scalar()
        )
        return int(current or 0) + 1

    def create(self, **values: Any) -> FullAnalysis:
        item = FullAnalysis(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def create_job(self, **values: Any) -> FullAnalysisJob:
        item = FullAnalysisJob(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def latest_job(self, full_analysis_id: uuid.UUID) -> FullAnalysisJob | None:
        return (
            self.db.query(FullAnalysisJob)
            .filter(FullAnalysisJob.full_analysis_id == full_analysis_id)
            .order_by(desc(FullAnalysisJob.queued_at))
            .first()
        )

    def replace_result_rows(
        self,
        *,
        full_analysis_id: uuid.UUID,
        case_id: uuid.UUID,
        rules: list[dict[str, Any]],
        calculations: list[dict[str, Any]],
        scenarios: list[dict[str, Any]],
        confidence_scores: list[dict[str, Any]],
        inconsistencies: list[dict[str, Any]],
    ) -> None:
        self.db.query(AnalysisInconsistency).filter(
            AnalysisInconsistency.full_analysis_id == full_analysis_id,
        ).delete()
        self.db.query(ConfidenceScore).filter(
            ConfidenceScore.full_analysis_id == full_analysis_id,
        ).delete()
        self.db.query(Scenario).filter(Scenario.full_analysis_id == full_analysis_id).delete()
        self.db.query(CalculationResult).filter(
            CalculationResult.full_analysis_id == full_analysis_id,
        ).delete()
        self.db.query(LegalRuleResult).filter(
            LegalRuleResult.full_analysis_id == full_analysis_id,
        ).delete()
        self.db.flush()

        for item in rules:
            self.db.add(
                LegalRuleResult(
                    full_analysis_id=full_analysis_id,
                    case_id=case_id,
                    rule_code=item["ruleCode"],
                    rule_name=item["ruleName"],
                    rule_version=item["ruleVersion"],
                    rule_category=item["ruleCategory"],
                    input_facts=item.get("inputFacts") or {},
                    source_refs=item.get("sourceRefs") or [],
                    condition_expression=item.get("conditionExpression"),
                    result=item["result"],
                    result_detail=item.get("resultDetail") or {},
                    explanation=item["explanation"],
                    confidence=item["confidence"],
                    requires_review=item.get("requiresReview", False),
                )
            )

        for item in calculations:
            self.db.add(
                CalculationResult(
                    full_analysis_id=full_analysis_id,
                    case_id=case_id,
                    calculation_code=item["calculationCode"],
                    calculation_name=item["calculationName"],
                    calculation_version=item["calculationVersion"],
                    calculation_type=item["calculationType"],
                    input_values=item.get("inputValues") or {},
                    formula_ref=item.get("formulaRef"),
                    formula_expression=item.get("formulaExpression"),
                    source_refs=item.get("sourceRefs") or [],
                    result_value=item.get("resultValue"),
                    result_unit=item.get("resultUnit"),
                    result_detail=item.get("resultDetail") or {},
                    confidence=item["confidence"],
                )
            )

        for item in scenarios:
            self.db.add(
                Scenario(
                    full_analysis_id=full_analysis_id,
                    case_id=case_id,
                    scenario_type=item["scenarioType"],
                    name=item["name"],
                    description=item["description"],
                    base_periods=item.get("basePeriods") or [],
                    legal_basis_refs=item.get("legalBasisRefs") or [],
                    calculation_refs=item.get("calculationRefs") or [],
                    amount_estimated=item.get("amountEstimated"),
                    weeks_estimated=item.get("weeksEstimated"),
                    retroactive_estimated=item.get("retroactiveEstimated"),
                    difference_vs_recognized=item.get("differenceVsRecognized"),
                    confidence=item["confidence"],
                )
            )

        for item in confidence_scores:
            self.db.add(
                ConfidenceScore(
                    full_analysis_id=full_analysis_id,
                    case_id=case_id,
                    scope=item["scope"],
                    scope_ref_id=_parse_uuid(item.get("scopeRefId")),
                    score=item["score"],
                    level=item["level"],
                    reasons=item.get("reasons") or [],
                    recommended_action=item.get("recommendedAction"),
                )
            )

        for item in inconsistencies:
            self.db.add(
                AnalysisInconsistency(
                    full_analysis_id=full_analysis_id,
                    case_id=case_id,
                    inconsistency_type=item["inconsistencyType"],
                    severity=item["severity"],
                    title=item["title"],
                    description=item["description"],
                    evidence_refs=item.get("evidenceRefs") or [],
                    legal_rule_refs=item.get("legalRuleRefs") or [],
                    calculation_refs=item.get("calculationRefs") or [],
                    economic_impact_estimated=item.get("economicImpactEstimated"),
                    legal_impact=item.get("legalImpact"),
                    missing_documents=item.get("missingDocuments"),
                    recommended_action=item.get("recommendedAction"),
                    confidence=item["confidence"],
                )
            )
        self.db.flush()

    def list_rule_results(
        self,
        *,
        full_analysis_id: uuid.UUID,
        category: str | None,
        result: str | None,
        requires_review: bool | None,
        page: int,
        limit: int,
    ) -> tuple[list[LegalRuleResult], int]:
        query = self.db.query(LegalRuleResult).filter(
            LegalRuleResult.full_analysis_id == full_analysis_id,
        )
        if category:
            query = query.filter(LegalRuleResult.rule_category == category)
        if result:
            query = query.filter(LegalRuleResult.result == result)
        if requires_review is not None:
            query = query.filter(LegalRuleResult.requires_review.is_(requires_review))
        total = query.with_entities(func.count(LegalRuleResult.id)).scalar() or 0
        items = (
            query.order_by(asc(LegalRuleResult.created_at), asc(LegalRuleResult.rule_code))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return items, total

    def list_calculations(
        self,
        *,
        full_analysis_id: uuid.UUID,
        calculation_type: str | None,
        page: int,
        limit: int,
    ) -> tuple[list[CalculationResult], int]:
        query = self.db.query(CalculationResult).filter(
            CalculationResult.full_analysis_id == full_analysis_id,
        )
        if calculation_type:
            query = query.filter(CalculationResult.calculation_type == calculation_type)
        total = query.with_entities(func.count(CalculationResult.id)).scalar() or 0
        items = (
            query.order_by(asc(CalculationResult.created_at), asc(CalculationResult.calculation_code))
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return items, total

    def list_scenarios(self, full_analysis_id: uuid.UUID) -> list[Scenario]:
        return (
            self.db.query(Scenario)
            .filter(Scenario.full_analysis_id == full_analysis_id)
            .order_by(asc(Scenario.created_at), asc(Scenario.scenario_type))
            .all()
        )

    def list_inconsistencies(self, full_analysis_id: uuid.UUID) -> list[AnalysisInconsistency]:
        return (
            self.db.query(AnalysisInconsistency)
            .filter(AnalysisInconsistency.full_analysis_id == full_analysis_id)
            .order_by(desc(AnalysisInconsistency.severity), asc(AnalysisInconsistency.created_at))
            .all()
        )

    def list_confidence_scores(self, full_analysis_id: uuid.UUID) -> list[ConfidenceScore]:
        return (
            self.db.query(ConfidenceScore)
            .filter(ConfidenceScore.full_analysis_id == full_analysis_id)
            .order_by(asc(ConfidenceScore.created_at), asc(ConfidenceScore.scope))
            .all()
        )


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
