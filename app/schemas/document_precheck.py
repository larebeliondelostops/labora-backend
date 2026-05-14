from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentPrecheckStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(alias="documentId")
    force: bool = False


class OcrPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_pages: int = Field(alias="maxPages", default=5, ge=1, le=20)
    include_text_preview: bool = Field(alias="includeTextPreview", default=True)
    force: bool = False


class ManualReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str
    traffic_light: str = Field(alias="trafficLight")
    notes: str | None = None
    issues_to_resolve: list[str] = Field(alias="issuesToResolve", default_factory=list)

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, value: str) -> str:
        allowed = {
            "suitable",
            "suitable_with_observations",
            "requires_reupload",
            "requires_human_review",
            "unsupported",
            "failed",
        }
        if value not in allowed:
            raise ValueError("decision invalida")
        return value

    @field_validator("traffic_light")
    @classmethod
    def validate_traffic_light(cls, value: str) -> str:
        if value not in {"green", "yellow", "red", "gray"}:
            raise ValueError("trafficLight invalido")
        return value


class DocumentIssueDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    severity: str
    page_number: int | None = Field(alias="pageNumber", default=None)
    title: str
    message: str
    suggested_action: str | None = Field(alias="suggestedAction", default=None)
    metadata: dict[str, Any] | None = None


class OcrPagePreviewDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page_number: int = Field(alias="pageNumber")
    text_preview: str | None = Field(alias="textPreview", default=None)
    confidence_score: float | None = Field(alias="confidenceScore", default=None)
    text_density: float | None = Field(alias="textDensity", default=None)
    is_blurry: bool = Field(alias="isBlurry")
    is_rotated: bool = Field(alias="isRotated")
    rotation_degrees: int | None = Field(alias="rotationDegrees", default=None)
    has_table_like_content: bool = Field(alias="hasTableLikeContent")
    issues: list[DocumentIssueDto] = Field(default_factory=list)


class OcrPreviewSummaryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ocr_job_id: str | None = Field(alias="ocrJobId", default=None)
    status: str
    engine: str | None = None
    pages_total: int | None = Field(alias="pagesTotal", default=None)
    pages_processed: int | None = Field(alias="pagesProcessed", default=None)
    text_detected: bool | None = Field(alias="textDetected", default=None)
    avg_text_density: float | None = Field(alias="avgTextDensity", default=None)
    pages: list[OcrPagePreviewDto] | None = None


class AiProviderSummaryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    model: str
    task: str
    confidence_score: float = Field(alias="confidenceScore")
    latency_ms: int | None = Field(alias="latencyMs", default=None)


class DocumentPrecheckDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    precheck_id: str = Field(alias="precheckId")
    case_id: str = Field(alias="caseId")
    document_id: str = Field(alias="documentId")
    document_name: str | None = Field(alias="documentName", default=None)
    status: str
    decision: str | None
    traffic_light: str = Field(alias="trafficLight")
    confidence_score: float | None = Field(alias="confidenceScore", default=None)
    summary: str | None = None
    issues: list[DocumentIssueDto] = Field(default_factory=list)
    ocr: OcrPreviewSummaryDto | None = None
    ai: AiProviderSummaryDto | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime | None = Field(alias="updatedAt", default=None)
    started_at: datetime | None = Field(alias="startedAt", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)
    failed_at: datetime | None = Field(alias="failedAt", default=None)


class DocumentPrecheckListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    items: list[dict]


class OcrPreviewStartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ocr_job_id: str = Field(alias="ocrJobId")
    document_id: str = Field(alias="documentId")
    status: str
