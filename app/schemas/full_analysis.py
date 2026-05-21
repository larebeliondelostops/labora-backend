from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


FullAnalysisStatus = Literal[
    "not_started",
    "queued",
    "in_progress",
    "rules_running",
    "calculations_running",
    "scenario_comparison_running",
    "confidence_evaluation_running",
    "requires_review",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]


class FullAnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    force_reprocess: bool = Field(alias="forceReprocess", default=False)
    reason: str = Field(default="user_requested", max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        return normalized or "user_requested"


class FullAnalysisRetryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class FullAnalysisReviewDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "needs_more_documents", "needs_recalculation"]
    notes: str | None = Field(default=None, max_length=2000)
    adjustments: list[dict[str, Any]] = Field(default_factory=list)


class FullAnalysisStartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    status: FullAnalysisStatus
    version: int
    message: str


class ProgressStepDto(BaseModel):
    key: str
    label: str
    status: Literal["pending", "active", "completed", "warning", "error", "blocked"]


class ProgressDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    percentage: int
    current_step: str = Field(alias="currentStep")
    steps: list[ProgressStepDto]


class ConfidenceSummaryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_score: float | None = Field(alias="globalScore")
    level: str
    requires_human_review: bool = Field(alias="requiresHumanReview")
    reasons: list[str] = Field(default_factory=list)


class FullAnalysisResultResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    case_id: str = Field(alias="caseId")
    status: FullAnalysisStatus
    version: int | None = None
    progress: ProgressDto
    executive_result: dict[str, Any] | None = Field(alias="executiveResult", default=None)
    legal_conclusion: str | None = Field(alias="legalConclusion", default=None)
    recommended_route: str | None = Field(alias="recommendedRoute", default=None)
    viability_level: str | None = Field(alias="viabilityLevel", default=None)
    confidence: ConfidenceSummaryDto
    created_at: datetime | None = Field(alias="createdAt", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)


class PaginationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: int
    limit: int
    page_size: int = Field(alias="pageSize")
    total: int


class RuleResultDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    rule_code: str = Field(alias="ruleCode")
    rule_name: str = Field(alias="ruleName")
    category: str
    result: str
    explanation: str
    source_refs: list[dict[str, Any]] = Field(alias="sourceRefs")
    confidence: float
    requires_review: bool = Field(alias="requiresReview")


class RuleResultsResponse(BaseModel):
    items: list[RuleResultDto]
    pagination: PaginationDto


class CalculationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    calculation_code: str = Field(alias="calculationCode")
    name: str
    type: str
    input_values: dict[str, Any] = Field(alias="inputValues")
    formula_ref: str | None = Field(alias="formulaRef", default=None)
    formula_expression: str | None = Field(alias="formulaExpression", default=None)
    source_refs: list[dict[str, Any]] = Field(alias="sourceRefs")
    result_value: float | None = Field(alias="resultValue")
    result_unit: str | None = Field(alias="resultUnit")
    result_detail: dict[str, Any] = Field(alias="resultDetail")
    confidence: float


class CalculationsResponse(BaseModel):
    items: list[CalculationDto]
    pagination: PaginationDto


class ScenarioDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    scenario_type: str = Field(alias="scenarioType")
    name: str
    description: str | None = None
    amount_estimated: float | None = Field(alias="amountEstimated")
    weeks_estimated: float | None = Field(alias="weeksEstimated")
    retroactive_estimated: float | None = Field(alias="retroactiveEstimated")
    difference_vs_recognized: float | None = Field(alias="differenceVsRecognized")
    confidence: float


class ScenariosResponse(BaseModel):
    items: list[ScenarioDto]


class InconsistencyDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    severity: str
    title: str
    description: str
    evidence_refs: list[dict[str, Any]] = Field(alias="evidenceRefs")
    legal_impact: str | None = Field(alias="legalImpact")
    economic_impact_estimated: float | None = Field(alias="economicImpactEstimated")
    missing_documents: list[str] = Field(alias="missingDocuments")
    recommended_action: str | None = Field(alias="recommendedAction")
    confidence: float


class InconsistenciesResponse(BaseModel):
    items: list[InconsistencyDto]


class ConfidenceScoreDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scope: str
    scope_ref_id: str | None = Field(alias="scopeRefId", default=None)
    score: float
    level: str
    reasons: list[str]
    recommended_action: str | None = Field(alias="recommendedAction", default=None)


class ConfidenceResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_score: float | None = Field(alias="globalScore")
    level: str
    requires_human_review: bool = Field(alias="requiresHumanReview")
    scores: list[ConfidenceScoreDto]
