from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class UTMParams(BaseModel):
    source: str | None = Field(default=None, max_length=255)
    medium: str | None = Field(default=None, max_length=255)
    campaign: str | None = Field(default=None, max_length=255)
    content: str | None = Field(default=None, max_length=255)
    term: str | None = Field(default=None, max_length=255)


class LeadCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(alias="fullName", min_length=4, max_length=255)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=30)
    service_interest: str | None = Field(default=None, alias="serviceInterest", max_length=100)
    message: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default="landing", max_length=50)
    accepted_privacy_notice: bool = Field(alias="acceptedPrivacyNotice")
    utm: UTMParams | None = None

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if len(normalized) < 4:
            raise ValueError("El nombre debe tener al menos 4 caracteres.")
        return normalized

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None

        prefix = "+" if stripped.startswith("+") else ""
        digits = "".join(character for character in stripped if character.isdigit())
        if len(digits) < 7:
            raise ValueError("El telefono debe tener al menos 7 digitos.")
        return f"{prefix}{digits}"

    @field_validator("accepted_privacy_notice")
    @classmethod
    def validate_privacy_notice(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Debes aceptar el aviso de privacidad para contacto.")
        return value


class NextAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str
    url: str


class LeadCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: str
    message: str
    next_action: NextAction = Field(alias="nextAction")


class PublicContentSection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    section_key: str = Field(alias="sectionKey")
    title: str | None = None
    subtitle: str | None = None
    body: dict | list | None = None
    cta_label: str | None = Field(default=None, alias="ctaLabel")
    cta_url: str | None = Field(default=None, alias="ctaUrl")


class PublicHomeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    page: str
    sections: list[PublicContentSection]
    legal_notice: str | None = Field(alias="legalNotice")
    updated_at: datetime = Field(alias="updatedAt")


class FaqResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    question: str
    answer: str
    category: str | None = None
    sort_order: int = Field(alias="sortOrder")


class FaqListResponse(BaseModel):
    items: list[FaqResponse]


class PublicEventCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_name: str = Field(alias="eventName", min_length=3, max_length=120)
    anonymous_id: str | None = Field(default=None, alias="anonymousId", max_length=120)
    lead_id: UUID | None = Field(default=None, alias="leadId")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_metadata(self) -> "PublicEventCreate":
        if self.metadata is None:
            return self

        sensitive_markers = {
            "password",
            "token",
            "secret",
            "document",
            "salary",
            "salario",
            "historia_laboral",
        }
        serialized = str(self.metadata).lower()
        if len(serialized) > 5000:
            raise ValueError("El metadata del evento es demasiado grande.")
        if any(marker in serialized for marker in sensitive_markers):
            raise ValueError("El metadata no debe incluir informacion sensible.")
        return self


class PublicEventResponse(BaseModel):
    id: str
    status: str = "completed"


class IntentClassificationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=3, max_length=1000)
    anonymous_id: str | None = Field(default=None, alias="anonymousId", max_length=120)


class IntentClassificationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    intent: str
    confidence: float
    requires_human_followup: bool = Field(alias="requiresHumanFollowup")
    safe_reply: str = Field(alias="safeReply")
