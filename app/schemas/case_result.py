from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ResultStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
    "blocked",
    "requires_review",
    "approved",
    "rejected",
    "error",
]

FinalViabilityLevel = Literal["high", "medium", "low", "incomplete", "not_applicable"]


class ResultGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    force_regenerate: bool = Field(alias="forceRegenerate", default=False)
    reason: str = Field(default="manual_request", max_length=500)
    source_analysis_id: str | None = Field(alias="sourceAnalysisId", default=None)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        return normalized or "manual_request"


class ResultGenerateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId")
    case_id: str = Field(alias="caseId")
    result_id: str = Field(alias="resultId")
    status: ResultStatus


class CaseResultUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    headline: str | None = Field(default=None, max_length=240)
    executive_summary: str | None = Field(alias="executiveSummary", default=None, max_length=5000)
    user_explanation: str | None = Field(alias="userExplanation", default=None, max_length=5000)
    legal_disclaimer: str | None = Field(alias="legalDisclaimer", default=None, max_length=3000)
    conclusion: str | None = Field(default=None, max_length=5000)
    status: ResultStatus | None = None
    requires_human_review: bool | None = Field(alias="requiresHumanReview", default=None)
    is_visible_to_user: bool | None = Field(alias="isVisibleToUser", default=None)
    review_comment: str | None = Field(alias="reviewComment", default=None, max_length=2000)


class ResultApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class ResultRejectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=3, max_length=2000)
    send_to_regeneration: bool = Field(alias="sendToRegeneration", default=False)


class FinalViabilityDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    level: FinalViabilityLevel
    label: str
    score: float | None = None
    color: str
    rationale: str
    strengths: list[Any] = Field(default_factory=list)
    weaknesses: list[Any] = Field(default_factory=list)
    missing_information: list[Any] = Field(alias="missingInformation", default_factory=list)


class EconomicEstimateDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    currency: str
    has_economic_estimate: bool = Field(alias="hasEconomicEstimate")
    estimated_claimable_amount: float | None = Field(alias="estimatedClaimableAmount", default=None)
    estimated_retroactive_amount: float | None = Field(alias="estimatedRetroactiveAmount", default=None)
    estimated_monthly_difference: float | None = Field(alias="estimatedMonthlyDifference", default=None)
    recognized_scenario_amount: float | None = Field(alias="recognizedScenarioAmount", default=None)
    corrected_scenario_amount: float | None = Field(alias="correctedScenarioAmount", default=None)
    min_amount: float | None = Field(alias="minAmount", default=None)
    max_amount: float | None = Field(alias="maxAmount", default=None)
    warnings: list[Any] = Field(default_factory=list)
    assumptions: list[Any] = Field(default_factory=list)


class MainInconsistencyDto(BaseModel):
    title: str
    description: str


class ResultCardDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    title: str
    value: str | None = None
    description: str | None = None
    icon: str | None = None
    tone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResultInconsistencyDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    title: str
    description: str
    evidence_summary: str | None = Field(alias="evidenceSummary", default=None)
    legal_impact: str | None = Field(alias="legalImpact", default=None)
    economic_impact: str | None = Field(alias="economicImpact", default=None)
    estimated_amount: float | None = Field(alias="estimatedAmount", default=None)
    confidence_score: float | None = Field(alias="confidenceScore", default=None)
    source_references: list[Any] = Field(alias="sourceReferences", default_factory=list)
    required_documents: list[Any] = Field(alias="requiredDocuments", default_factory=list)


class RecommendedRouteDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    route_type: str = Field(alias="routeType")
    title: str
    description: str
    priority: int
    next_action_label: str | None = Field(alias="nextActionLabel", default=None)
    next_action_type: str | None = Field(alias="nextActionType", default=None)
    next_action_url: str | None = Field(alias="nextActionUrl", default=None)
    requires_documents: bool = Field(alias="requiresDocuments")
    requires_professional_review: bool = Field(alias="requiresProfessionalReview")
    can_generate_legal_action: bool = Field(alias="canGenerateLegalAction")
    recommended_legal_action_type: str | None = Field(alias="recommendedLegalActionType", default=None)
    rationale: str
    blockers: list[Any] = Field(default_factory=list)
    required_documents: list[Any] = Field(alias="requiredDocuments", default_factory=list)


class ResultActionDto(BaseModel):
    type: str
    label: str
    enabled: bool
    href: str | None = None
    reason: str | None = None


class CaseResultResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    case_code: str = Field(alias="caseCode")
    result_id: str | None = Field(alias="resultId")
    version: int | None
    status: ResultStatus
    is_visible_to_user: bool = Field(alias="isVisibleToUser")
    generated_at: datetime | None = Field(alias="generatedAt")
    approved_at: datetime | None = Field(alias="approvedAt", default=None)
    headline: str | None = None
    executive_summary: str | None = Field(alias="executiveSummary", default=None)
    user_explanation: str | None = Field(alias="userExplanation", default=None)
    legal_disclaimer: str | None = Field(alias="legalDisclaimer", default=None)
    final_viability: FinalViabilityDto | None = Field(alias="finalViability", default=None)
    economic_estimate: EconomicEstimateDto | None = Field(alias="economicEstimate", default=None)
    main_inconsistency: MainInconsistencyDto | None = Field(alias="mainInconsistency", default=None)
    cards: list[ResultCardDto] = Field(default_factory=list)
    inconsistencies: list[ResultInconsistencyDto] = Field(default_factory=list)
    recommended_route: RecommendedRouteDto | None = Field(alias="recommendedRoute", default=None)
    missing_documents: list[Any] = Field(alias="missingDocuments", default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    blockers: list[Any] = Field(default_factory=list)
    available_actions: list[ResultActionDto] = Field(alias="availableActions", default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)


class ResultStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    status: ResultStatus
    progress: int
    is_visible_to_user: bool = Field(alias="isVisibleToUser")
    blockers: list[dict[str, Any]] = Field(default_factory=list)
