from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


DeliveryPackageStatus = Literal[
    "not_started",
    "generating",
    "ready",
    "partially_ready",
    "blocked",
    "requires_review",
    "completed",
    "closed",
    "error",
]
DownloadFileStatus = Literal[
    "pending",
    "available",
    "locked",
    "requires_review",
    "expired",
    "deleted",
    "error",
]
ShareLinkStatus = Literal[
    "active",
    "expired",
    "revoked",
    "max_views_reached",
    "disabled",
]
CaseClosureStatus = Literal["open", "closure_requested", "closed", "reopened"]
SharePermission = Literal[
    "view",
    "view_only",
    "download",
    "comment",
    "upload_supporting_files",
]
DownloadFileCategory = Literal[
    "executive_report",
    "technical_report",
    "inconsistency_matrix",
    "calculation_sheet",
    "legal_claim",
    "petition",
    "lawsuit_draft",
    "attachments_index",
    "traceability_log",
    "supporting_document",
    "other",
]
ClosureReason = Literal[
    "user_completed_download",
    "user_no_longer_needs_service",
    "case_finished",
    "duplicate_case",
    "admin_decision",
    "other",
]


class DeliveryPackageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: DeliveryPackageStatus
    version: int
    title: str
    description: str | None = None
    completed_at: datetime | None = Field(alias="completedAt", default=None)
    closed_at: datetime | None = Field(alias="closedAt", default=None)
    ai_summary: str | None = Field(alias="aiSummary", default=None)
    ai_confidence: float | None = Field(alias="aiConfidence", default=None)
    ai_next_steps: list[dict[str, Any]] = Field(alias="aiNextSteps", default_factory=list)


class DownloadFileDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    file_name: str = Field(alias="fileName")
    category: DownloadFileCategory
    mime_type: str = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes")
    status: DownloadFileStatus
    is_unlocked: bool = Field(alias="isUnlocked")
    requires_review: bool = Field(alias="requiresReview")
    download_count: int = Field(alias="downloadCount")
    last_downloaded_at: datetime | None = Field(alias="lastDownloadedAt", default=None)


class ShareLinkDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    recipient_email: str | None = Field(alias="recipientEmail", default=None)
    status: ShareLinkStatus
    permissions: list[str]
    expires_at: datetime = Field(alias="expiresAt")
    view_count: int = Field(alias="viewCount")
    max_views: int | None = Field(alias="maxViews", default=None)


class DeliveryEventDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    event_type: str = Field(alias="eventType")
    actor_role: str = Field(alias="actorRole")
    previous_status: str | None = Field(alias="previousStatus", default=None)
    new_status: str | None = Field(alias="newStatus", default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(alias="createdAt")


class AvailableActionsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    can_download: bool = Field(alias="canDownload")
    can_create_share_link: bool = Field(alias="canCreateShareLink")
    can_complement_case: bool = Field(alias="canComplementCase")
    can_close_case: bool = Field(alias="canCloseCase")


class DeliveryCenterResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    package: DeliveryPackageDto
    files: list[DownloadFileDto]
    share_links: list[ShareLinkDto] = Field(alias="shareLinks")
    timeline: list[DeliveryEventDto]
    available_actions: AvailableActionsDto = Field(alias="availableActions")


class FileDownloadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    download_url: str = Field(alias="downloadUrl")
    expires_in_seconds: int = Field(alias="expiresInSeconds")


class CreateShareLinkRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    recipient_name: str | None = Field(alias="recipientName", default=None, max_length=180)
    recipient_email: str | None = Field(alias="recipientEmail", default=None, max_length=255)
    permissions: list[SharePermission]
    allowed_file_ids: list[str] | None = Field(alias="allowedFileIds", default=None)
    expires_at: datetime = Field(alias="expiresAt")
    max_views: int | None = Field(alias="maxViews", default=None, ge=1, le=500)

    @field_validator("permissions")
    @classmethod
    def normalize_permissions(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            permission = str(item).strip()
            if permission and permission not in seen:
                normalized.append(permission)
                seen.add(permission)
        if not normalized:
            raise ValueError("Debe indicar al menos un permiso.")
        if "view_only" in normalized and "view" not in normalized:
            normalized.insert(0, "view")
        return normalized

    @field_validator("allowed_file_ids")
    @classmethod
    def normalize_allowed_files(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            file_id = str(item).strip()
            if file_id and file_id not in seen:
                normalized.append(file_id)
                seen.add(file_id)
        return normalized


class CreateShareLinkResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    share_url: str = Field(alias="shareUrl")
    status: ShareLinkStatus
    expires_at: datetime = Field(alias="expiresAt")
    permissions: list[str]


class SharedPackageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_public_code: str = Field(alias="casePublicCode")
    status: DeliveryPackageStatus
    title: str
    owner_display_name: str = Field(alias="ownerDisplayName")


class SharedFileDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    file_name: str = Field(alias="fileName")
    category: DownloadFileCategory
    mime_type: str = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes")
    can_download: bool = Field(alias="canDownload")


class SharedDeliveryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package: SharedPackageDto
    files: list[SharedFileDto]
    permissions: list[str]
    expires_at: datetime = Field(alias="expiresAt")


class RevokeShareLinkResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: ShareLinkStatus
    revoked_at: datetime = Field(alias="revokedAt")


class ComplementDeliveryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=3, max_length=80)
    message: str | None = Field(default=None, max_length=2000)
    requested_document_types: list[str] = Field(alias="requestedDocumentTypes", default_factory=list)

    @field_validator("requested_document_types")
    @classmethod
    def normalize_document_types(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            key = str(item).strip()
            if key and key not in seen:
                normalized.append(key)
                seen.add(key)
        return normalized


class ComplementDeliveryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    status: DeliveryPackageStatus
    message: str


class CloseCaseRequest(BaseModel):
    reason: ClosureReason
    notes: str | None = Field(default=None, max_length=2000)


class CloseCaseResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    closure_status: CaseClosureStatus = Field(alias="closureStatus")
    closed_at: datetime = Field(alias="closedAt")


class DeliveryEventsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[DeliveryEventDto]
    next_cursor: str | None = Field(alias="nextCursor", default=None)


class AiSummaryRequest(BaseModel):
    force: bool = False


class AiSummaryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package_id: str = Field(alias="packageId")
    status: DeliveryPackageStatus
    ai_summary: str = Field(alias="aiSummary")
    ai_next_steps: list[dict[str, Any]] = Field(alias="aiNextSteps")
    ai_confidence: float = Field(alias="aiConfidence")
