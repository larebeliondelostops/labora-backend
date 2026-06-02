from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExtractionStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
    "blocked",
    "requires_review",
    "error",
]
ExtractionConfirmationStatus = Literal[
    "draft",
    "ai_extracted",
    "user_reviewing",
    "user_confirmed",
    "confirmed_with_pending_fields",
    "admin_review_required",
    "admin_approved",
    "rejected",
    "superseded",
]
ExtractionFieldStatus = Literal[
    "extracted",
    "normalized",
    "low_confidence",
    "corrected_by_user",
    "corrected_by_admin",
    "pending_user_confirmation",
    "confirmed",
    "ignored",
    "conflict",
]


class ExtractionRunCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_ids: list[str] = Field(alias="documentIds", default_factory=list)
    questionnaire_response_id: str | None = Field(
        alias="questionnaireResponseId",
        default=None,
    )
    mode: Literal["initial", "reprocess", "normalize_only"] = "initial"
    ai_provider: Literal["deepseek", "kimi", "openai", "mock", "none"] = Field(
        alias="aiProvider",
        default="mock",
    )

    @field_validator("document_ids")
    @classmethod
    def strip_document_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("questionnaire_response_id")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ExtractionFieldUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    field_id: str = Field(alias="fieldId")
    entity_type: str | None = Field(alias="entityType", default=None, max_length=80)
    entity_id: str | None = Field(alias="entityId", default=None)
    field_key: str = Field(alias="fieldKey", min_length=1, max_length=120)
    new_value: Any = Field(alias="newValue")
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("field_id", "entity_type", "entity_id", "field_key", "reason")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.strip().split())
        return stripped or None


class ExtractionFieldsPatchRequest(BaseModel):
    updates: list[ExtractionFieldUpdateInput] = Field(min_length=1, max_length=100)


class ManualEmployerCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=2, max_length=180)
    nit: str | None = Field(default=None, max_length=40)
    employer_type: Literal["private", "public", "teacher", "unknown"] | None = Field(
        alias="employerType",
        default="unknown",
    )
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "nit", "reason")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class ManualLaborPeriodCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    employer_id: str | None = Field(alias="employerId", default=None)
    start_date: date = Field(alias="startDate")
    end_date: date | None = Field(alias="endDate", default=None)
    period_type: Literal["worked", "contributed", "reported", "gap", "unknown"] = Field(
        alias="periodType",
    )
    weeks_detected: float | None = Field(alias="weeksDetected", default=None, ge=0)
    regime_hint: Literal["general", "public", "teacher", "special", "unknown"] | None = Field(
        alias="regimeHint",
        default="unknown",
    )
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_date_range(self) -> "ManualLaborPeriodCreateRequest":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("endDate debe ser mayor o igual a startDate.")
        return self

    @field_validator("employer_id", "reason")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.strip().split())
        return stripped or None


class IgnoreEntityRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ConfirmExtractionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    accept_low_confidence_fields: bool = Field(
        alias="acceptLowConfidenceFields",
        default=False,
    )
    mark_pending_fields: bool = Field(alias="markPendingFields", default=False)
    user_statement: str | None = Field(alias="userStatement", default=None, max_length=2000)

    @field_validator("user_statement")
    @classmethod
    def normalize_statement(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class IssueResolveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["resolved", "dismissed"]
    resolution_note: str | None = Field(
        alias="resolutionNote",
        default=None,
        max_length=1000,
    )


class ExtractionSummaryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    employers_count: int = Field(alias="employersCount")
    labor_periods_count: int = Field(alias="laborPeriodsCount")
    contribution_weeks_total: float = Field(alias="contributionWeeksTotal")
    salary_bases_count: int = Field(alias="salaryBasesCount")
    gaps_count: int = Field(alias="gapsCount")
    novelties_count: int = Field(alias="noveltiesCount")


class ExtractedFieldDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    entity_type: str = Field(alias="entityType")
    entity_id: str | None = Field(alias="entityId")
    field_key: str = Field(alias="fieldKey")
    raw_value: str | None = Field(alias="rawValue")
    normalized_value: Any | None = Field(alias="normalizedValue")
    display_value: str | None = Field(alias="displayValue")
    confidence: float | None = None
    status: str
    source_document_id: str | None = Field(alias="sourceDocumentId")
    source_page: int | None = Field(alias="sourcePage")
    source_bbox: dict | None = Field(alias="sourceBbox")
    source_text: str | None = Field(alias="sourceText")
    extraction_method: str = Field(alias="extractionMethod")
    needs_review: bool = Field(alias="needsReview")


class EmployerDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    raw_name: str | None = Field(alias="rawName")
    nit: str | None = None
    employer_type: str | None = Field(alias="employerType")
    confidence: float | None = None
    status: str
    source: str


class LaborPeriodDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    employer_id: str | None = Field(alias="employerId")
    employer_name: str | None = Field(alias="employerName")
    start_date: date = Field(alias="startDate")
    end_date: date | None = Field(alias="endDate")
    period_type: str = Field(alias="periodType")
    regime_hint: str | None = Field(alias="regimeHint")
    weeks_detected: float | None = Field(alias="weeksDetected")
    days_detected: int | None = Field(alias="daysDetected")
    salary_base_detected: float | None = Field(alias="salaryBaseDetected")
    novelty: str | None = None
    confidence: float | None = None
    status: str
    source_document_id: str | None = Field(alias="sourceDocumentId")
    source_page: int | None = Field(alias="sourcePage")
    source: dict | None = None


class ContributionWeekDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    labor_period_id: str | None = Field(alias="laborPeriodId")
    employer_id: str | None = Field(alias="employerId")
    employer_name: str | None = Field(alias="employerName", default=None)
    year: int
    month: int | None = None
    weeks: float
    days: int | None = None
    source: str
    confidence: float | None = None
    status: str


class SalaryBaseDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    labor_period_id: str | None = Field(alias="laborPeriodId")
    employer_id: str | None = Field(alias="employerId")
    employer_name: str | None = Field(alias="employerName", default=None)
    year: int
    month: int | None = None
    period_year: int = Field(alias="periodYear")
    period_month: int | None = Field(alias="periodMonth")
    amount: float
    original_value: float | None = Field(alias="originalValue", default=None)
    normalized_value: float | None = Field(alias="normalizedValue", default=None)
    currency: str
    raw_value: str | None = Field(alias="rawValue")
    confidence: float | None = None
    status: str


class ContributionGapDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")
    days: int | None = None
    weeks: float | None = None
    reason: str | None = None
    gap_type: str = Field(alias="gapType")
    description: str | None = None
    severity: str
    confidence: float | None = None
    status: str


class LaborNoveltyDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    labor_period_id: str | None = Field(alias="laborPeriodId")
    novelty_type: str = Field(alias="noveltyType")
    description: str
    detected_by: str = Field(alias="detectedBy")
    confidence: float | None = None
    status: str


class ExtractionIssueDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    severity: str
    message: str
    field_id: str | None = Field(alias="fieldId")
    entity_type: str | None = Field(alias="entityType")
    entity_id: str | None = Field(alias="entityId")
    resolved: bool
    status: str


class TimelineItemDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    start_date: date = Field(alias="startDate")
    end_date: date | None = Field(alias="endDate")
    employer_name: str | None = Field(alias="employerName")
    weeks: float | None = None
    confidence: float | None = None
    status: str
    issues: list[dict] = Field(default_factory=list)


class DocumentReferenceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(alias="documentId")
    document_name: str | None = Field(alias="documentName", default=None)
    page: int | None = None
    source_text: str | None = Field(alias="sourceText", default=None)
    bbox: dict | None = None
    field_id: str | None = Field(alias="fieldId", default=None)


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    status: str
    module_status: str = Field(alias="moduleStatus")
    confirmation_status: str = Field(alias="confirmationStatus")
    confidence_avg: float | None = Field(alias="confidenceAvg")
    low_confidence_count: int = Field(alias="lowConfidenceCount")
    summary: ExtractionSummaryDto
    employers: list[EmployerDto]
    labor_periods: list[LaborPeriodDto] = Field(alias="laborPeriods")
    contribution_weeks: list[ContributionWeekDto] = Field(alias="contributionWeeks")
    salary_bases: list[SalaryBaseDto] = Field(alias="salaryBases")
    gaps: list[ContributionGapDto]
    novelties: list[LaborNoveltyDto]
    fields: list[ExtractedFieldDto]
    issues: list[ExtractionIssueDto]
    timeline: list[TimelineItemDto]
    tables: dict[str, list[dict]]
    document_references: list[DocumentReferenceDto] = Field(alias="documentReferences")
    actions: dict[str, bool]
    can_confirm: bool = Field(alias="canConfirm")
    blocking_reasons: list[str] = Field(alias="blockingReasons")


class ExtractionRunStartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    extraction_run_id: str = Field(alias="extractionRunId")
    status: str
    job_id: str = Field(alias="jobId")


class PatchFieldsResponse(BaseModel):
    updated: int
    corrections: list[dict]


class IgnoreEntityResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_type: str = Field(alias="entityType")
    entity_id: str = Field(alias="entityId")
    status: str


class ConfirmExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    confirmation_status: str = Field(alias="confirmationStatus")
    confirmed_at: datetime = Field(alias="confirmedAt")
    next_step: str = Field(alias="nextStep")


class CorrectionItemDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    field_key: str = Field(alias="fieldKey")
    previous_value: Any | None = Field(alias="previousValue")
    new_value: Any = Field(alias="newValue")
    reason: str | None = None
    corrected_by: dict = Field(alias="correctedBy")
    created_at: datetime = Field(alias="createdAt")


class CorrectionsListResponse(BaseModel):
    items: list[CorrectionItemDto]
    pagination: dict


class IssuesListResponse(BaseModel):
    items: list[ExtractionIssueDto]


class IssueResolveResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: str
    resolved_at: datetime | None = Field(alias="resolvedAt")
