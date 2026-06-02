import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


DocumentType = Literal["CC", "CE", "PA", "NIT", "OTHER"]
HolderType = Literal["self", "third_party"]
ThirdPartyRelationship = Literal[
    "child",
    "spouse",
    "lawyer",
    "relative",
    "authorized_agent",
    "other",
]
ThirdPartyAuthorizationStatus = Literal[
    "not_required",
    "pending",
    "uploaded",
    "verified",
    "rejected",
]
CaseType = Literal[
    "labor_history_analysis",
    "pension_liquidation_review",
    "pension_reliquidation",
    "missing_weeks_review",
    "public_service_time_review",
    "teacher_magisterio_case",
    "special_regime_case",
    "administrative_claim",
    "lawsuit_draft_preparation",
    "not_sure",
]
SituationType = Literal[
    "not_pensioned_yet",
    "pensioned_with_doubts",
    "request_denied",
    "recognized_with_possible_error",
    "reliquidation_needed",
    "missing_weeks_or_time",
    "employer_default_or_omission",
    "regime_transfer_issue",
    "other",
    "not_sure",
]
CaseStatus = Literal[
    "draft",
    "created",
    "consent_pending",
    "ready_for_documents",
    "documents_pending",
    "documents_uploaded",
    "document_validation_running",
    "document_validation_failed",
    "extraction_running",
    "extraction_review_required",
    "assumptions_required",
    "pension_simulation_running",
    "pension_simulation_ready",
    "pension_simulation_failed",
    "legal_route_suggested",
    "legal_document_paywall",
    "payment_pending",
    "payment_approved",
    "payment_rejected",
    "preanalysis_pending",
    "preanalysis_ready",
    "preview_locked",
    "paid_unlocked",
    "template_selection_required",
    "legal_draft_running",
    "legal_draft_review_required",
    "legal_draft_ready",
    "professional_review_requested",
    "delivered",
    "analysis_in_progress",
    "completed",
    "requires_review",
    "blocked",
    "closed",
    "archived",
    "error",
]
SourceModule = Literal[
    "cases",
    "documents",
    "preanalysis",
    "paywall",
    "payments",
    "pension_simulation",
    "analysis",
    "reports",
    "legal_actions",
]
ActorRole = Literal["user", "admin", "legal_reviewer", "system"]
CaseOwnerRole = Literal[
    "owner",
    "creator",
    "authorized_user",
    "legal_reviewer",
    "admin_assignee",
]
CaseTagSource = Literal["user", "admin", "ai", "system"]
HistorySort = Literal["asc", "desc"]


class CaseHolderInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_name: str = Field(alias="firstName", min_length=2, max_length=120)
    last_name: str = Field(alias="lastName", min_length=2, max_length=120)
    document_type: DocumentType = Field(alias="documentType")
    document_number: str = Field(alias="documentNumber", min_length=2, max_length=80)
    birth_date: date | None = Field(alias="birthDate", default=None)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("document_type", mode="before")
    @classmethod
    def normalize_document_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("document_number")
    @classmethod
    def normalize_document_number(cls, value: str) -> str:
        return re.sub(r"\s+", "", value.strip()).upper()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value)


class CaseHolderUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_name: str | None = Field(alias="firstName", default=None, min_length=2, max_length=120)
    last_name: str | None = Field(alias="lastName", default=None, min_length=2, max_length=120)
    document_type: DocumentType | None = Field(alias="documentType", default=None)
    document_number: str | None = Field(alias="documentNumber", default=None, min_length=2, max_length=80)
    birth_date: date | None = Field(alias="birthDate", default=None)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.strip().split())

    @field_validator("document_type", mode="before")
    @classmethod
    def normalize_document_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @field_validator("document_number")
    @classmethod
    def normalize_document_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return re.sub(r"\s+", "", value.strip()).upper()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value)


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    holder_type: HolderType = Field(alias="holderType")
    holder: CaseHolderInput
    acting_as_third_party: bool = Field(alias="actingAsThirdParty", default=False)
    third_party_relationship: ThirdPartyRelationship | None = Field(
        alias="thirdPartyRelationship",
        default=None,
    )
    case_type_requested: CaseType = Field(alias="caseTypeRequested")
    pension_fund_or_entity: str | None = Field(
        alias="pensionFundOrEntity",
        default=None,
        max_length=160,
    )
    situation_type: SituationType = Field(alias="situationType")

    @model_validator(mode="after")
    def validate_third_party(self) -> "CaseCreateRequest":
        if self.acting_as_third_party and self.third_party_relationship is None:
            raise ValueError("thirdPartyRelationship is required for third-party cases.")
        return self

    @field_validator("pension_fund_or_entity")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class CaseUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    holder: CaseHolderUpdate | None = None
    holder_type: HolderType | None = Field(alias="holderType", default=None)
    acting_as_third_party: bool | None = Field(alias="actingAsThirdParty", default=None)
    third_party_relationship: ThirdPartyRelationship | None = Field(
        alias="thirdPartyRelationship",
        default=None,
    )
    case_type_requested: CaseType | None = Field(alias="caseTypeRequested", default=None)
    pension_fund_or_entity: str | None = Field(
        alias="pensionFundOrEntity",
        default=None,
        max_length=160,
    )
    situation_type: SituationType | None = Field(alias="situationType", default=None)

    @field_validator("pension_fund_or_entity")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class CaseCloseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=3, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class InternalCaseStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    new_status: CaseStatus = Field(alias="newStatus")
    reason: str | None = Field(default=None, max_length=1000)
    source_module: SourceModule = Field(alias="sourceModule")
    metadata: dict = Field(default_factory=dict)


class InternalAiSuggestionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    case_type_suggested: CaseType = Field(alias="caseTypeSuggested")
    confidence: float = Field(ge=0, le=1)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(min_length=1, max_length=120)


class AdminAssignCaseRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignee_user_id: str = Field(alias="assigneeUserId")
    role: Literal["legal_reviewer", "admin_assignee"]


class AdminCaseTagRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=80)
    source: CaseTagSource = "admin"

    @field_validator("tag")
    @classmethod
    def normalize_tag(cls, value: str) -> str:
        tag = _normalize_tag(value)
        if not tag:
            raise ValueError("Invalid tag.")
        return tag


class Pagination(BaseModel):
    page: int
    page_size: int = Field(alias="pageSize")
    total: int


class CaseCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_number: str = Field(alias="caseNumber")
    status: str
    current_step: str = Field(alias="currentStep")
    next_best_action: str = Field(alias="nextBestAction")
    created_at: datetime = Field(alias="createdAt")


class CaseUpdateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_number: str = Field(alias="caseNumber")
    status: str
    updated_at: datetime = Field(alias="updatedAt")


class CaseSubmitResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: str
    current_step: str = Field(alias="currentStep")
    next_best_action: str = Field(alias="nextBestAction")


class CaseCloseResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: str
    closed_at: datetime = Field(alias="closedAt")


class CaseListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_number: str = Field(alias="caseNumber")
    holder_full_name: str = Field(alias="holderFullName")
    case_type_requested: str = Field(alias="caseTypeRequested")
    status: str
    current_step: str = Field(alias="currentStep")
    next_best_action: str = Field(alias="nextBestAction")
    allowed_actions: list[str] = Field(alias="allowedActions")
    updated_at: datetime = Field(alias="updatedAt")


class CaseListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    data: list[CaseListItem]
    pagination: Pagination


class CaseHolderMaskedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    document_type: str = Field(alias="documentType")
    document_number_masked: str = Field(alias="documentNumberMasked")
    birth_date: date | None = Field(alias="birthDate", default=None)
    email_masked: str | None = Field(alias="emailMasked", default=None)
    phone_masked: str | None = Field(alias="phoneMasked", default=None)


class CaseDetailResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    case_number: str = Field(alias="caseNumber")
    holder_type: str = Field(alias="holderType")
    holder: CaseHolderMaskedResponse
    acting_as_third_party: bool = Field(alias="actingAsThirdParty")
    third_party_relationship: str | None = Field(alias="thirdPartyRelationship")
    third_party_authorization_status: str = Field(alias="thirdPartyAuthorizationStatus")
    case_type_requested: str = Field(alias="caseTypeRequested")
    case_type_suggested: str | None = Field(alias="caseTypeSuggested")
    case_type_confidence: float | None = Field(alias="caseTypeConfidence")
    pension_fund_or_entity: str | None = Field(alias="pensionFundOrEntity")
    situation_type: str = Field(alias="situationType")
    status: str
    status_reason: str | None = Field(alias="statusReason")
    current_step: str = Field(alias="currentStep")
    next_best_action: str = Field(alias="nextBestAction")
    allowed_actions: list[str] = Field(alias="allowedActions")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AdminCaseDetailResponse(CaseDetailResponse):
    owner_user_id: str = Field(alias="ownerUserId")
    holder_document_number: str = Field(alias="holderDocumentNumber")
    holder_email: str | None = Field(alias="holderEmail")
    holder_phone: str | None = Field(alias="holderPhone")
    tags: list[str] = Field(default_factory=list)


class CaseHistoryItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    event_type: str = Field(alias="eventType")
    title: str
    description: str | None = None
    severity: str
    created_at: datetime = Field(alias="createdAt")


class CaseHistoryResponse(BaseModel):
    data: list[CaseHistoryItem]


class InternalCaseStatusUpdateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    previous_status: str | None = Field(alias="previousStatus")
    new_status: str = Field(alias="newStatus")


def _normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    prefix = "+" if stripped.startswith("+") else ""
    digits = "".join(character for character in stripped if character.isdigit())
    if len(digits) < 7:
        raise ValueError("Invalid phone number.")
    return f"{prefix}{digits}"


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


def _normalize_tag(value: str) -> str:
    tag = re.sub(r"[^a-z0-9_:-]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", tag).strip("_")
