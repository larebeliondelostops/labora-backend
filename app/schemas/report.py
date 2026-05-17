from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ReportType = Literal[
    "executive",
    "technical",
    "calculation",
    "inconsistency_matrix",
    "full",
]
ReportStatus = Literal[
    "draft",
    "queued",
    "generating",
    "ready",
    "requires_review",
    "approved",
    "rejected",
    "archived",
    "failed",
]
ExportFormat = Literal["pdf", "docx"]
ExportStatus = Literal["queued", "generating", "ready", "failed", "expired"]
ReportVersionStatus = Literal["current", "superseded", "archived"]
OutputMode = Literal["async", "sync"]


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_type: ReportType = Field(alias="reportType", default="full")
    template_key: str | None = Field(alias="templateKey", default=None, max_length=120)
    force_regenerate: bool = Field(alias="forceRegenerate", default=False)
    include_sections: list[str] = Field(alias="includeSections", default_factory=list)
    output_mode: OutputMode = Field(alias="outputMode", default="async")

    @field_validator("include_sections")
    @classmethod
    def normalize_sections(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            key = item.strip()
            if key and key not in seen:
                normalized.append(key)
                seen.add(key)
        return normalized


class ReportListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    title: str
    report_type: ReportType = Field(alias="reportType")
    status: ReportStatus
    current_version_id: str | None = Field(alias="currentVersionId", default=None)
    version_number: int | None = Field(alias="versionNumber", default=None)
    requires_human_review: bool = Field(alias="requiresHumanReview")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class PaginationDto(BaseModel):
    page: int
    limit: int
    total: int


class ReportListResponse(BaseModel):
    items: list[ReportListItem]
    pagination: PaginationDto


class ReportCreateQueuedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_id: str = Field(alias="reportId")
    case_id: str = Field(alias="caseId")
    status: ReportStatus
    job_id: str | None = Field(alias="jobId", default=None)
    message: str


class ReportCreateReadyResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_id: str = Field(alias="reportId")
    case_id: str = Field(alias="caseId")
    report_type: ReportType = Field(alias="reportType")
    status: ReportStatus
    current_version_id: str | None = Field(alias="currentVersionId", default=None)
    requires_human_review: bool = Field(alias="requiresHumanReview")


class ReportCurrentVersionDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    version_number: int = Field(alias="versionNumber")
    created_at: datetime = Field(alias="createdAt")


class ReportSectionDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    section_key: str = Field(alias="sectionKey")
    title: str
    content_markdown: str = Field(alias="contentMarkdown")
    content_json: dict[str, Any] | None = Field(alias="contentJson", default=None)
    order_index: int = Field(alias="orderIndex")
    confidence: float | None = None
    source_refs: list[dict[str, Any]] = Field(alias="sourceRefs", default_factory=list)


class ReportAvailableExportDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    format: ExportFormat
    status: ExportStatus
    file_name: str = Field(alias="fileName")


class ReportTraceabilityDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    content_hash: str | None = Field(alias="contentHash", default=None)
    source_hash: str | None = Field(alias="sourceHash", default=None)
    generated_at: datetime | None = Field(alias="generatedAt", default=None)


class ReportDetailResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    title: str
    report_type: ReportType = Field(alias="reportType")
    status: ReportStatus
    current_version: ReportCurrentVersionDto | None = Field(alias="currentVersion", default=None)
    sections: list[ReportSectionDto]
    available_exports: list[ReportAvailableExportDto] = Field(alias="availableExports")
    traceability: ReportTraceabilityDto


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: ExportFormat
    version_id: str | None = Field(alias="versionId", default=None)
    include_traceability_stamp: bool = Field(alias="includeTraceabilityStamp", default=True)
    include_evidence_index: bool = Field(alias="includeEvidenceIndex", default=True)


class ReportExportResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    export_file_id: str = Field(alias="exportFileId")
    report_id: str = Field(alias="reportId")
    version_id: str = Field(alias="versionId")
    format: ExportFormat
    status: ExportStatus


class ExportDownloadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    download_url: str = Field(alias="downloadUrl")
    expires_at: datetime = Field(alias="expiresAt")


class ReportVersionDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    version_number: int = Field(alias="versionNumber")
    status: ReportVersionStatus
    change_summary: str | None = Field(alias="changeSummary", default=None)
    created_at: datetime = Field(alias="createdAt")
    created_by_role: str | None = Field(alias="createdByRole", default=None)


class ReportVersionsResponse(BaseModel):
    items: list[ReportVersionDto]


class ReportApproveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    review_notes: str | None = Field(alias="reviewNotes", default=None, max_length=2000)


class ReportRejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ReportPayload(BaseModel):
    report_type: str
