from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AdminAssignCaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assigned_to_admin_id: str | None = Field(alias="assignedToAdminId", default=None)
    assignment_type: str | None = Field(alias="assignmentType", default=None)
    reason: str | None = Field(default=None, max_length=1000)
    assignee_user_id: str | None = Field(alias="assigneeUserId", default=None)
    role: str | None = None

    @model_validator(mode="after")
    def normalize_legacy_payload(self) -> "AdminAssignCaseRequest":
        if self.assigned_to_admin_id is None and self.assignee_user_id is not None:
            self.assigned_to_admin_id = self.assignee_user_id
        if self.assignment_type is None and self.role is not None:
            self.assignment_type = self.role
        if self.assigned_to_admin_id is None:
            raise ValueError("assignedToAdminId is required.")
        if self.assignment_type is None:
            raise ValueError("assignmentType is required.")
        return self


class AdminCaseStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    admin_status: str = Field(alias="adminStatus", min_length=2, max_length=80)
    reason: str | None = Field(default=None, max_length=1200)
    blocking: bool = False


class InternalNoteCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    note_type: str = Field(alias="noteType", min_length=2, max_length=60)
    body: str = Field(min_length=1, max_length=8000)
    visibility: Literal["internal", "publishable", "published_to_user"] = "internal"
    related_entity_type: str | None = Field(alias="relatedEntityType", default=None, max_length=80)
    related_entity_id: str | None = Field(alias="relatedEntityId", default=None)


class DocumentReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str = Field(min_length=2, max_length=60)
    observations: str | None = Field(default=None, max_length=4000)
    requires_reupload: bool = Field(alias="requiresReupload", default=False)
    requires_supporting_documents: bool = Field(alias="requiresSupportingDocuments", default=False)
    requested_documents: list[str] = Field(alias="requestedDocuments", default_factory=list)

    @field_validator("requested_documents")
    @classmethod
    def trim_requested_documents(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ExtractionCorrectionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    field: str = Field(min_length=1, max_length=120)
    old_value: Any = Field(alias="oldValue", default=None)
    new_value: Any = Field(alias="newValue")
    reason: str = Field(min_length=3, max_length=1200)


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str = Field(min_length=2, max_length=60)
    reason: str | None = Field(default=None, max_length=4000)
    requires_human_review: bool = Field(alias="requiresHumanReview", default=False)
    resolved_alert_ids: list[str] = Field(alias="resolvedAlertIds", default_factory=list)
    blocking: bool = False


class ReportApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str = Field(min_length=2, max_length=60)
    reason: str | None = Field(default=None, max_length=4000)
    visible_to_user: bool = Field(alias="visibleToUser", default=False)


class LegalDraftReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str = Field(min_length=2, max_length=60)
    reason: str | None = Field(default=None, max_length=4000)
    quality_checklist: dict[str, Any] = Field(alias="qualityChecklist", default_factory=dict)


class OverrideUnlockRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    unlock_full_analysis: bool = Field(alias="unlockFullAnalysis")
    reason: str = Field(min_length=3, max_length=1200)


class ResolveAiAlertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resolution: str = Field(min_length=3, max_length=2000)
    keep_warning_in_user_report: bool = Field(alias="keepWarningInUserReport", default=False)


class Pagination(BaseModel):
    page: int
    limit: int
    total: int
    page_size: int = Field(alias="pageSize")


class DashboardSummaryResponse(BaseModel):
    totals: dict[str, int]
    by_stage: list[dict[str, Any]] = Field(alias="byStage")
    sla: dict[str, int]


class AdminCaseListResponse(BaseModel):
    data: list[dict[str, Any]]
    pagination: Pagination


class AdminAssignmentResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignment_id: str = Field(alias="assignmentId")
    case_id: str = Field(alias="caseId")
    assigned_to_admin_id: str = Field(alias="assignedToAdminId")
    assignment_type: str = Field(alias="assignmentType")
    status: str


class AdminStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    admin_status: str = Field(alias="adminStatus")
    previous_admin_status: str | None = Field(alias="previousAdminStatus")
    blocking: bool
    updated_at: datetime = Field(alias="updatedAt")
