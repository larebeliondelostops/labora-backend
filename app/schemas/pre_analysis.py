from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PreAnalysisStatus = Literal[
    "not_started",
    "queued",
    "in_progress",
    "completed",
    "blocked",
    "requires_review",
    "error",
]
TrafficLight = Literal["green", "yellow", "red", "gray"]
ViabilityLevel = Literal["high", "medium", "low", "insufficient"]


class PreAnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    force_regenerate: bool = Field(alias="forceRegenerate", default=False)
    source: str = Field(default="user_request", max_length=80)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        return normalized or "user_request"


class PreAnalysisRetryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PreAnalysisReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["completed", "requires_review"]
    review_notes: str | None = Field(alias="reviewNotes", default=None, max_length=2000)
    traffic_light: TrafficLight | None = Field(alias="trafficLight", default=None)
    viability_level: ViabilityLevel | None = Field(alias="viabilityLevel", default=None)


class PreAnalysisStartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pre_analysis_id: str = Field(alias="preAnalysisId")
    case_id: str = Field(alias="caseId")
    status: PreAnalysisStatus
    message: str | None = None
    polling_url: str | None = Field(alias="pollingUrl", default=None)
    result_available: bool | None = Field(alias="resultAvailable", default=None)


class PreIssueDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    severity: Literal["low", "medium", "high"]
    title: str
    public_summary: str = Field(alias="publicSummary")
    locked_detail_available: bool = Field(alias="lockedDetailAvailable")
    confidence: float | None = None


class MissingDocumentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    document_type: str = Field(alias="documentType")
    title: str
    priority: Literal["required", "recommended", "optional"]
    reason: str | None = None
    status: str
    upload_hint: str | None = Field(alias="uploadHint", default=None)


class ValueDetectedDto(BaseModel):
    title: str | None = None
    summary: str | None = None


class CtaDto(BaseModel):
    type: str
    label: str
    description: str


class WarningDto(BaseModel):
    code: str
    message: str


class ReviewGuidanceActionDto(BaseModel):
    code: str
    label: str
    description: str


class ReviewGuidanceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason_code: str = Field(alias="reasonCode")
    title: str
    message: str
    confidence_threshold: float = Field(alias="confidenceThreshold")
    actions: list[ReviewGuidanceActionDto] = Field(default_factory=list)


class PreAnalysisResultResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    case_id: str = Field(alias="caseId")
    status: PreAnalysisStatus
    blocked_reason: str | None = Field(alias="blockedReason", default=None)
    traffic_light: TrafficLight | None = Field(alias="trafficLight", default=None)
    viability_level: ViabilityLevel | None = Field(alias="viabilityLevel", default=None)
    completion_score: float | None = Field(alias="completionScore", default=None)
    confidence: float | None = None
    preliminary_case_type: str | None = Field(alias="preliminaryCaseType", default=None)
    limited_summary: str | None = Field(alias="limitedSummary", default=None)
    value_detected: ValueDetectedDto = Field(alias="valueDetected")
    issues: list[PreIssueDto] = Field(default_factory=list)
    missing_documents: list[MissingDocumentDto] = Field(
        alias="missingDocuments",
        default_factory=list,
    )
    cta: CtaDto | None = None
    warnings: list[WarningDto] = Field(default_factory=list)
    review_guidance: ReviewGuidanceDto | None = Field(alias="reviewGuidance", default=None)
    created_at: datetime | None = Field(alias="createdAt", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)


class PreAnalysisStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    pre_analysis_id: str | None = Field(alias="preAnalysisId", default=None)
    status: PreAnalysisStatus
    progress: int
    current_step: str = Field(alias="currentStep")
    can_retry: bool = Field(alias="canRetry")


class AdminPreAnalysisListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    user_id: str = Field(alias="userId")
    status: PreAnalysisStatus
    traffic_light: str | None = Field(alias="trafficLight")
    viability_level: str | None = Field(alias="viabilityLevel")
    confidence: float | None
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(alias="completedAt")


class AdminPreAnalysisListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[AdminPreAnalysisListItem]
    pagination: dict[str, Any]
