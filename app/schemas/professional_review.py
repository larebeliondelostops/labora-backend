from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProfessionalReviewStatus = Literal[
    "not_started",
    "payment_pending",
    "requested",
    "queued",
    "assigned",
    "in_review",
    "changes_requested",
    "client_action_required",
    "ready_for_approval",
    "approved",
    "completed",
    "rejected",
    "cancelled",
    "blocked",
    "error",
]
ReviewType = Literal[
    "report_review",
    "legal_draft_review",
    "lawsuit_draft_review",
    "claim_review",
    "petition_review",
    "calculation_review",
    "full_case_review",
]
TargetType = Literal[
    "report",
    "legal_draft",
    "generated_file",
    "case_result",
    "calculation",
]
ReviewPriority = Literal["low", "normal", "high", "urgent"]
RiskLevel = Literal["low", "medium", "high", "critical"]
AssignmentResponse = Literal["accepted", "rejected"]
CommentVisibility = Literal["internal", "client_visible", "lawyer_only", "admin_only"]
CommentType = Literal[
    "general",
    "legal_observation",
    "correction_request",
    "missing_document",
    "risk_alert",
    "approval_note",
]
ReviewedFileType = Literal[
    "reviewed_report_pdf",
    "reviewed_report_docx",
    "reviewed_claim_docx",
    "reviewed_lawsuit_docx",
    "reviewed_petition_docx",
    "comparison_pdf",
    "lawyer_notes_pdf",
]
ReviewedFileStatus = Literal[
    "draft",
    "ready_for_approval",
    "approved",
    "published",
    "rejected",
    "archived",
]


class ProfessionalReviewCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_type: TargetType = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    review_type: ReviewType = Field(alias="reviewType")
    client_notes: str | None = Field(alias="clientNotes", default=None, max_length=4000)
    priority: ReviewPriority = "normal"
    requires_payment: bool = Field(alias="requiresPayment", default=False)
    amount_cop: int | None = Field(alias="amountCop", default=None, ge=0)


class ProfessionalReviewCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    status: ProfessionalReviewStatus
    requires_payment: bool = Field(alias="requiresPayment")
    payment_order_id: str | None = Field(alias="paymentOrderId", default=None)
    next_action: str = Field(alias="nextAction")


class ProfessionalReviewListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    case_number: str = Field(alias="caseNumber")
    client_name: str | None = Field(alias="clientName", default=None)
    review_type: str = Field(alias="reviewType")
    target_type: str = Field(alias="targetType")
    status: str
    priority: str
    assigned_lawyer_name: str | None = Field(alias="assignedLawyerName", default=None)
    due_at: datetime | None = Field(alias="dueAt", default=None)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class PaginationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: int
    page_size: int = Field(alias="pageSize")
    total: int


class ProfessionalReviewListResponse(BaseModel):
    items: list[ProfessionalReviewListItem]
    pagination: PaginationDto


class ReviewAssignmentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    lawyer_id: str = Field(alias="lawyerId")
    lawyer_name: str | None = Field(alias="lawyerName", default=None)
    assignment_status: str = Field(alias="assignmentStatus")
    assigned_by: str | None = Field(alias="assignedBy", default=None)
    assigned_at: datetime = Field(alias="assignedAt")
    accepted_at: datetime | None = Field(alias="acceptedAt", default=None)
    rejected_at: datetime | None = Field(alias="rejectedAt", default=None)
    rejection_reason: str | None = Field(alias="rejectionReason", default=None)
    assignment_notes: str | None = Field(alias="assignmentNotes", default=None)


class LawyerCommentDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    author_id: str = Field(alias="authorId")
    visibility: str
    comment_type: str = Field(alias="commentType")
    body: str
    target_section: str | None = Field(alias="targetSection", default=None)
    target_file_id: str | None = Field(alias="targetFileId", default=None)
    target_version_id: str | None = Field(alias="targetVersionId", default=None)
    resolved: bool
    resolved_by: str | None = Field(alias="resolvedBy", default=None)
    resolved_at: datetime | None = Field(alias="resolvedAt", default=None)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ReviewedFileDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    source_file_id: str | None = Field(alias="sourceFileId", default=None)
    generated_file_id: str | None = Field(alias="generatedFileId", default=None)
    uploaded_file_id: str | None = Field(alias="uploadedFileId", default=None)
    file_type: str = Field(alias="fileType")
    version_number: int = Field(alias="versionNumber")
    status: str
    checksum: str | None = None
    mime_type: str = Field(alias="mimeType")
    file_size: int = Field(alias="fileSize")
    approved_by: str | None = Field(alias="approvedBy", default=None)
    approved_at: datetime | None = Field(alias="approvedAt", default=None)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AuditEventDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    event_name: str = Field(alias="eventName")
    previous_status: str | None = Field(alias="previousStatus", default=None)
    new_status: str | None = Field(alias="newStatus", default=None)
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")


class ProfessionalReviewDetailResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    case_number: str = Field(alias="caseNumber")
    client_id: str = Field(alias="clientId")
    requested_by: str = Field(alias="requestedBy")
    status: str
    review_type: str = Field(alias="reviewType")
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    priority: str
    requires_payment: bool = Field(alias="requiresPayment")
    payment_order_id: str | None = Field(alias="paymentOrderId", default=None)
    summary_for_reviewer: str | None = Field(alias="summaryForReviewer", default=None)
    client_notes: str | None = Field(alias="clientNotes", default=None)
    internal_notes: str | None = Field(alias="internalNotes", default=None)
    due_at: datetime | None = Field(alias="dueAt", default=None)
    started_at: datetime | None = Field(alias="startedAt", default=None)
    approved_at: datetime | None = Field(alias="approvedAt", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)
    cancelled_at: datetime | None = Field(alias="cancelledAt", default=None)
    cancellation_reason: str | None = Field(alias="cancellationReason", default=None)
    blocked_reason: str | None = Field(alias="blockedReason", default=None)
    risk_level: str | None = Field(alias="riskLevel", default=None)
    ai_confidence: float | None = Field(alias="aiConfidence", default=None)
    case: dict[str, Any]
    target: dict[str, Any]
    assignment: ReviewAssignmentDto | None = None
    comments: list[LawyerCommentDto]
    reviewed_files: list[ReviewedFileDto] = Field(alias="reviewedFiles")
    audit_events: list[AuditEventDto] = Field(alias="auditEvents")
    next_actions: list[str] = Field(alias="nextActions")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ProfessionalReviewPatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: ProfessionalReviewStatus | None = None
    priority: ReviewPriority | None = None
    internal_notes: str | None = Field(alias="internalNotes", default=None, max_length=4000)
    due_at: datetime | None = Field(alias="dueAt", default=None)
    blocked_reason: str | None = Field(alias="blockedReason", default=None, max_length=4000)
    risk_level: RiskLevel | None = Field(alias="riskLevel", default=None)


class AssignReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lawyer_id: str = Field(alias="lawyerId")
    due_at: datetime | None = Field(alias="dueAt", default=None)
    assignment_notes: str | None = Field(alias="assignmentNotes", default=None, max_length=2000)


class AssignReviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignment_id: str = Field(alias="assignmentId")
    status: str
    lawyer_id: str = Field(alias="lawyerId")


class AssignmentResponseRequest(BaseModel):
    response: AssignmentResponse
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_required_when_rejected(cls, value: str | None, info) -> str | None:
        if info.data.get("response") == "rejected" and not (value or "").strip():
            raise ValueError("reason es requerido al rechazar la asignacion.")
        return value


class CreateCommentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    visibility: CommentVisibility = "client_visible"
    comment_type: CommentType = Field(alias="commentType", default="general")
    body: str = Field(min_length=1, max_length=8000)
    target_section: str | None = Field(alias="targetSection", default=None, max_length=120)
    target_file_id: str | None = Field(alias="targetFileId", default=None)
    target_version_id: str | None = Field(alias="targetVersionId", default=None)


class ResolveCommentRequest(BaseModel):
    resolved: bool


class RequestClientActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=3, max_length=4000)
    required_documents: list[dict[str, Any]] = Field(alias="requiredDocuments", default_factory=list)


class ReviewedFileCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_file_id: str | None = Field(alias="sourceFileId", default=None)
    generated_file_id: str | None = Field(alias="generatedFileId", default=None)
    uploaded_file_id: str | None = Field(alias="uploadedFileId", default=None)
    file_type: ReviewedFileType = Field(alias="fileType")
    version_number: int | None = Field(alias="versionNumber", default=None, ge=1)
    status: ReviewedFileStatus = "ready_for_approval"
    storage_path: str | None = Field(alias="storagePath", default=None, max_length=1000)
    mime_type: str | None = Field(alias="mimeType", default=None, max_length=120)
    file_size: int | None = Field(alias="fileSize", default=None, ge=0)
    checksum: str | None = Field(default=None, max_length=128)


class ApproveReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reviewed_file_id: str | None = Field(alias="reviewedFileId", default=None)
    approval_note: str | None = Field(alias="approvalNote", default=None, max_length=4000)
    publish_to_client: bool = Field(alias="publishToClient", default=True)
    approve_original: bool = Field(alias="approveOriginal", default=False)


class RejectReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=3, max_length=4000)
    client_visible_message: str | None = Field(alias="clientVisibleMessage", default=None, max_length=4000)


class CancelReviewRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=4000)


class AiSummaryResponse(BaseModel):
    summary: str
    alerts: list[dict[str, Any]]
    confidence: float
    disclaimer: str
