from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ConsentType = Literal[
    "terms_and_conditions",
    "personal_data_processing",
    "sensitive_data_processing",
    "electronic_means",
    "ai_scope_acknowledgement",
]
LegalDocumentStatus = Literal["draft", "active", "archived"]
ConsentSource = Literal["web", "mobile_web", "admin", "system"]
ComplianceStatus = Literal[
    "not_started",
    "in_progress",
    "completed",
    "blocked",
    "requires_review",
    "error",
]


class LegalDocumentCurrent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    type: str
    title: str
    version: str
    hash_sha256: str = Field(alias="hashSha256")
    content_markdown: str = Field(alias="contentMarkdown")
    is_required: bool = Field(alias="isRequired")
    effective_from: datetime = Field(alias="effectiveFrom")


class LegalDocumentAdmin(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    type: str
    title: str
    slug: str
    version: str
    hash_sha256: str = Field(alias="hashSha256")
    status: str
    is_required: bool = Field(alias="isRequired")
    effective_from: datetime = Field(alias="effectiveFrom")
    effective_to: datetime | None = Field(alias="effectiveTo", default=None)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ConsentStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: ComplianceStatus
    can_upload_documents: bool = Field(alias="canUploadDocuments")
    required_consent_types: list[str] = Field(alias="requiredConsentTypes")
    accepted_consent_types: list[str] = Field(alias="acceptedConsentTypes")
    missing_consent_types: list[str] = Field(alias="missingConsentTypes")
    last_accepted_at: datetime | None = Field(alias="lastAcceptedAt", default=None)


class ConsentSubmitItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    legal_document_id: str = Field(alias="legalDocumentId")
    consent_type: ConsentType = Field(alias="consentType")
    accepted: bool


class ConsentSubmitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[ConsentSubmitItem] = Field(min_length=1)
    source: ConsentSource = "web"
    locale: str | None = Field(default=None, max_length=20)


class AcceptedConsentResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    consent_type: str = Field(alias="consentType")
    document_version: str = Field(alias="documentVersion")
    accepted_at: datetime = Field(alias="acceptedAt")
    evidence_hash_sha256: str = Field(alias="evidenceHashSha256")


class ConsentSubmitResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: ComplianceStatus
    can_upload_documents: bool = Field(alias="canUploadDocuments")
    accepted_consents: list[AcceptedConsentResponse] = Field(alias="acceptedConsents")


class ConsentHistoryItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    consent_type: str = Field(alias="consentType")
    title: str
    document_version: str = Field(alias="documentVersion")
    accepted_at: datetime = Field(alias="acceptedAt")
    document_hash_sha256: str = Field(alias="documentHashSha256")
    evidence_hash_sha256: str = Field(alias="evidenceHashSha256")


class DocumentUploadPermission(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    allowed: bool
    reason: str | None = None
    missing_consent_types: list[str] = Field(
        alias="missingConsentTypes",
        default_factory=list,
    )


class LegalDocumentCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: ConsentType
    title: str = Field(min_length=3, max_length=255)
    slug: str | None = Field(default=None, max_length=160)
    content_markdown: str = Field(alias="contentMarkdown", min_length=1)
    content_plain_text: str | None = Field(alias="contentPlainText", default=None)
    version: str = Field(min_length=1, max_length=50)
    status: LegalDocumentStatus = "draft"
    is_required: bool = Field(alias="isRequired", default=True)
    effective_from: datetime = Field(alias="effectiveFrom")
