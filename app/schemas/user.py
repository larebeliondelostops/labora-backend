from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


DOCUMENT_TYPES = {"CC", "CE", "TI", "PASSPORT", "NIT", "OTHER"}


class UserCreate(BaseModel):
    first_name: str
    last_name: str
    document_type: str
    document_number: str
    phone: str | None = None
    email: EmailStr
    password: str = Field(min_length=8)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class AccountUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    first_name: str | None = Field(alias="firstName")
    last_name: str | None = Field(alias="lastName")
    full_name: str | None = Field(default=None, alias="fullName")
    avatar_url: str | None = Field(default=None, alias="avatarUrl")
    document_type: str | None = Field(default=None, alias="documentType")
    document_number_masked: str | None = Field(default=None, alias="documentNumberMasked")
    email: EmailStr
    phone_masked: str | None = Field(default=None, alias="phoneMasked")
    status: str
    email_verified: bool = Field(alias="emailVerified")
    phone_verified: bool = Field(default=False, alias="phoneVerified")
    requires_otp: bool = Field(default=False, alias="requiresOtp")
    registration_completed: bool = Field(default=False, alias="registrationCompleted")
    next_step: str = Field(alias="nextStep")
    roles: list[str]
    created_at: datetime | None = Field(default=None, alias="createdAt")


class CurrentUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    email: EmailStr
    first_name: str | None = Field(alias="firstName")
    last_name: str | None = Field(alias="lastName")
    full_name: str | None = Field(alias="fullName")
    avatar_url: str | None = Field(alias="avatarUrl")
    role: str
    is_active: bool = Field(alias="isActive")
    is_verified: bool = Field(alias="isVerified")


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_name: str | None = Field(default=None, alias="firstName", min_length=2, max_length=120)
    last_name: str | None = Field(default=None, alias="lastName", min_length=2, max_length=120)
    document_type: str | None = Field(default=None, alias="documentType")
    document_number: str | None = Field(default=None, alias="documentNumber")
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.strip().split())

    @field_validator("document_type")
    @classmethod
    def validate_document_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in DOCUMENT_TYPES:
            raise ValueError("Tipo de documento invalido.")
        return normalized

    @field_validator("document_number")
    @classmethod
    def normalize_document_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(
            character for character in value.strip() if character.isalnum()
        ).upper()
        if len(normalized) < 3:
            raise ValueError("Numero de documento invalido.")
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
            raise ValueError("Telefono invalido.")
        return f"{prefix}{digits}"


class UserSessionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    device_name: str | None = Field(default=None, alias="deviceName")
    ip_address_masked: str | None = Field(default=None, alias="ipAddressMasked")
    created_at: datetime = Field(alias="createdAt")
    last_used_at: datetime | None = Field(default=None, alias="lastUsedAt")
    current: bool
