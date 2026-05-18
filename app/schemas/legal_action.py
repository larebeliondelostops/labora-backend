from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


LegalActionType = Literal[
    "technical_report_download",
    "executive_summary",
    "petition",
    "administrative_claim",
    "reliquidation_request",
    "administrative_appeal",
    "lawsuit_draft",
    "professional_review_request",
]
LegalActionStatus = Literal[
    "not_started",
    "in_progress",
    "generated",
    "requires_review",
    "blocked",
    "completed",
    "cancelled",
    "error",
]
LegalActionEligibilityStatus = Literal[
    "available",
    "recommended",
    "not_recommended",
    "blocked",
    "requires_more_data",
    "requires_professional_review",
]
LegalDraftStatus = Literal[
    "created",
    "generating",
    "ready_for_edit",
    "editing",
    "quality_check_pending",
    "quality_check_failed",
    "quality_check_passed",
    "requires_review",
    "approved",
    "export_ready",
    "exported",
    "failed",
    "archived",
]
DraftSectionStatus = Literal[
    "pending",
    "generating",
    "generated",
    "edited",
    "approved",
    "needs_data",
    "low_confidence",
    "failed",
]
DraftExportFormat = Literal["pdf", "docx"]
ProfessionalReviewLevel = Literal["none", "optional", "recommended", "mandatory"]
GenerationMode = Literal["ai_generated", "template_only", "manual"]
OutputMode = Literal["async", "sync"]
ReviewDecision = Literal["approved", "changes_requested", "rejected"]


class LegalActionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action_type: LegalActionType = Field(alias="actionType")
    selected_by_user: bool = Field(alias="selectedByUser", default=True)


class LegalActionAvailableItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action_type: LegalActionType = Field(alias="actionType")
    status: LegalActionEligibilityStatus
    reason: str | None = None
    professional_review_level: ProfessionalReviewLevel = Field(alias="professionalReviewLevel")
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    pending_data: list[dict[str, Any]] = Field(alias="pendingData", default_factory=list)
    missing_attachments: list[dict[str, Any]] = Field(alias="missingAttachments", default_factory=list)
    recommended: bool = False


class LegalActionsAvailableResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    source_analysis_id: str = Field(alias="sourceAnalysisId")
    source_report_id: str = Field(alias="sourceReportId")
    actions: list[LegalActionAvailableItem]


class LegalActionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    user_id: str = Field(alias="userId")
    action_type: LegalActionType = Field(alias="actionType")
    status: LegalActionStatus
    eligibility_status: LegalActionEligibilityStatus = Field(alias="eligibilityStatus")
    eligibility_reason: str | None = Field(alias="eligibilityReason", default=None)
    professional_review_level: ProfessionalReviewLevel = Field(alias="professionalReviewLevel")
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    pending_data: list[dict[str, Any]] = Field(alias="pendingData", default_factory=list)
    missing_attachments: list[dict[str, Any]] = Field(alias="missingAttachments", default_factory=list)
    selected_by_user: bool = Field(alias="selectedByUser")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class LegalActionListResponse(BaseModel):
    items: list[LegalActionResponse]


class DraftCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    generation_mode: GenerationMode = Field(alias="generationMode", default="template_only")
    user_inputs: dict[str, Any] = Field(alias="userInputs", default_factory=dict)
    document_metadata: dict[str, Any] = Field(alias="documentMetadata", default_factory=dict)
    output_mode: OutputMode = Field(alias="outputMode", default="async")


class DraftCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    draft_id: str = Field(alias="draftId")
    status: LegalDraftStatus
    job_id: str | None = Field(alias="jobId", default=None)
    poll_url: str | None = Field(alias="pollUrl", default=None)


class DraftSectionDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    section_key: str = Field(alias="sectionKey")
    title: str
    order_index: int = Field(alias="orderIndex")
    content_html: str | None = Field(alias="contentHtml", default=None)
    content_plain: str | None = Field(alias="contentPlain", default=None)
    status: DraftSectionStatus
    source_references: list[dict[str, Any]] = Field(alias="sourceReferences", default_factory=list)
    pending_markers: list[dict[str, Any]] = Field(alias="pendingMarkers", default_factory=list)
    confidence_score: float | None = Field(alias="confidenceScore", default=None)
    generated_by_ai: bool = Field(alias="generatedByAi")


class DraftQualityCheckDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    overall_status: str = Field(alias="overallStatus")
    score: float | None = None
    checks: list[dict[str, Any]]
    critical_warnings: list[dict[str, Any]] = Field(alias="criticalWarnings", default_factory=list)
    created_at: datetime = Field(alias="createdAt")


class DraftExportDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    format: DraftExportFormat
    file_name: str = Field(alias="fileName")
    status: str
    version_number: int = Field(alias="versionNumber")
    download_url: str | None = Field(alias="downloadUrl", default=None)
    created_at: datetime = Field(alias="createdAt")


class DraftDetailResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    legal_action_id: str = Field(alias="legalActionId")
    title: str
    status: LegalDraftStatus
    professional_review_level: ProfessionalReviewLevel = Field(alias="professionalReviewLevel")
    quality_score: float | None = Field(alias="qualityScore", default=None)
    document_metadata: dict[str, Any] = Field(alias="documentMetadata")
    user_inputs: dict[str, Any] = Field(alias="userInputs")
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[DraftSectionDto]
    quality_check: DraftQualityCheckDto | None = Field(alias="qualityCheck", default=None)
    exports: list[DraftExportDto] = Field(default_factory=list)
    current_version_number: int = Field(alias="currentVersionNumber")
    is_locked: bool = Field(alias="isLocked")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class DraftSectionPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    content_html: str = Field(alias="contentHtml", min_length=1)


class DraftUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=3, max_length=255)
    document_metadata: dict[str, Any] | None = Field(alias="documentMetadata", default=None)
    sections: list[DraftSectionPatch] = Field(default_factory=list)
    change_summary: str | None = Field(alias="changeSummary", default=None, max_length=2000)


class DraftUpdateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    draft_id: str = Field(alias="draftId")
    status: LegalDraftStatus
    version_number: int = Field(alias="versionNumber")
    updated_at: datetime = Field(alias="updatedAt")


class SectionRegenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    instruction: str = Field(min_length=3, max_length=1200)
    preserve_user_edits: bool = Field(alias="preserveUserEdits", default=True)


class SectionRegenerateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(alias="sectionId")
    status: DraftSectionStatus
    job_id: str = Field(alias="jobId")


class QualityCheckResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    draft_id: str = Field(alias="draftId")
    status: LegalDraftStatus
    job_id: str = Field(alias="jobId")


class DraftExportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: DraftExportFormat
    include_watermark: bool = Field(alias="includeWatermark", default=False)


class DraftExportResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    export_id: str = Field(alias="exportId")
    status: str
    job_id: str = Field(alias="jobId")


class DraftExportsResponse(BaseModel):
    exports: list[DraftExportDto]


class ExportDownloadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    download_url: str = Field(alias="downloadUrl")
    expires_at: datetime = Field(alias="expiresAt")


class SubmitReviewRequest(BaseModel):
    message: str = Field(min_length=3, max_length=2000)
    priority: str = Field(default="normal", max_length=30)


class SubmitReviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    draft_id: str = Field(alias="draftId")
    status: LegalDraftStatus
    review_request_id: str = Field(alias="reviewRequestId")


class AdminLegalDraftItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    draft_id: str = Field(alias="draftId")
    case_id: str = Field(alias="caseId")
    case_number: str = Field(alias="caseNumber")
    action_type: LegalActionType = Field(alias="actionType")
    status: LegalDraftStatus
    quality_score: float | None = Field(alias="qualityScore", default=None)
    professional_review_level: ProfessionalReviewLevel = Field(alias="professionalReviewLevel")
    updated_at: datetime = Field(alias="updatedAt")


class PaginationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: int
    page_size: int = Field(alias="pageSize")
    total: int


class AdminLegalDraftsResponse(BaseModel):
    items: list[AdminLegalDraftItem]
    pagination: PaginationDto


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: ReviewDecision
    review_notes: str | None = Field(alias="reviewNotes", default=None, max_length=2000)

    @field_validator("review_notes")
    @classmethod
    def notes_required_for_rejection(cls, value: str | None, info) -> str | None:
        decision = info.data.get("decision")
        if decision in {"changes_requested", "rejected"} and not (value or "").strip():
            raise ValueError("reviewNotes es requerido para devolver o rechazar.")
        return value


class ReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    draft_id: str = Field(alias="draftId")
    status: LegalDraftStatus
    reviewed_at: datetime = Field(alias="reviewedAt")


class LegalActionPayload(BaseModel):
    action_type: str
