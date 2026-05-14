from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


DocumentStatus = Literal[
    "draft",
    "uploading",
    "uploaded",
    "processing",
    "validated",
    "requires_review",
    "rejected",
    "replaced",
    "deleted",
    "failed",
]
DocumentValidationStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
    "blocked",
    "requires_review",
    "error",
]


class DocumentCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    original_filename: str = Field(alias="originalFilename", min_length=1, max_length=255)
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=120)
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    document_type_code: str | None = Field(
        alias="documentTypeCode",
        default=None,
        max_length=80,
    )
    is_primary: bool = Field(alias="isPrimary", default=False)

    @field_validator("original_filename", "mime_type", "document_type_code")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DocumentUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str | None = Field(alias="displayName", default=None, max_length=255)
    document_type_code: str | None = Field(
        alias="documentTypeCode",
        default=None,
        max_length=80,
    )
    is_primary: bool | None = Field(alias="isPrimary", default=None)

    @field_validator("display_name", "document_type_code")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = " ".join(value.strip().split())
        return stripped or None


class DocumentReplaceRequest(DocumentCreateRequest):
    pass


class DocumentTypeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    name: str
    category: str
    is_required_for_basic_flow: bool = Field(alias="isRequiredForBasicFlow")
    is_primary_candidate: bool = Field(alias="isPrimaryCandidate")
    allowed_mime_types: list[str] = Field(alias="allowedMimeTypes")
    max_size_mb: int = Field(alias="maxSizeMb")


class DocumentSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_id: str = Field(alias="caseId")
    original_filename: str = Field(alias="originalFilename")
    display_name: str | None = Field(alias="displayName")
    document_type_code: str | None = Field(alias="documentTypeCode")
    status: str
    validation_status: str = Field(alias="validationStatus")
    is_primary: bool = Field(alias="isPrimary")
    created_at: datetime = Field(alias="createdAt")


class DocumentListResponse(BaseModel):
    items: list[dict]
    pagination: dict


class DocumentCreateResponse(BaseModel):
    document: dict
    upload: dict


class DocumentCompleteUploadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(alias="documentId")
    status: str
    validation_status: str = Field(alias="validationStatus")
    jobs: list[dict]


class DocumentViewUrlResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str
    expires_in_seconds: int = Field(alias="expiresInSeconds")


class DocumentReadinessResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_id: str = Field(alias="caseId")
    readiness_status: str = Field(alias="readinessStatus")
    has_primary_labor_history: bool = Field(alias="hasPrimaryLaborHistory")
    documents_total: int = Field(alias="documentsTotal")
    documents_validated: int = Field(alias="documentsValidated")
    documents_with_warnings: int = Field(alias="documentsWithWarnings")
    documents_rejected: int = Field(alias="documentsRejected")
    blocking_issues: list[dict] = Field(alias="blockingIssues")
    warnings: list[dict]
    next_action: str = Field(alias="nextAction")
