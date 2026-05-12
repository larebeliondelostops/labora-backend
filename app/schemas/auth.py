from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from typing import Any

from app.core.security import validate_password_strength


DOCUMENT_TYPES = {"CC", "CE", "TI", "PASSPORT", "NIT", "OTHER"}
EMAIL_OTP_PURPOSES = {"register", "login", "password_reset", "email_change"}


class DataResponse(BaseModel):
    data: Any


class UserRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    first_name: str = Field(alias="firstName", min_length=2, max_length=120)
    last_name: str = Field(alias="lastName", min_length=2, max_length=120)
    document_type: str = Field(alias="documentType")
    document_number: str = Field(alias="documentNumber", min_length=3, max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=30)
    password: str

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("document_type")
    @classmethod
    def validate_document_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in DOCUMENT_TYPES:
            raise ValueError("Tipo de documento invalido.")
        return normalized

    @field_validator("document_number")
    @classmethod
    def normalize_document_number(cls, value: str) -> str:
        return "".join(character for character in value.strip() if character.isalnum()).upper()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

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

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class VerifyOTPRequest(BaseModel):
    recipient: EmailStr
    purpose: str
    code: str = Field(min_length=6, max_length=6)

    @field_validator("recipient")
    @classmethod
    def normalize_recipient(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in EMAIL_OTP_PURPOSES:
            raise ValueError("Proposito OTP de correo invalido.")
        return normalized

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("El OTP debe contener solo numeros.")
        return value


class ResendOTPRequest(BaseModel):
    recipient: EmailStr
    purpose: str

    @field_validator("recipient")
    @classmethod
    def normalize_recipient(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in EMAIL_OTP_PURPOSES:
            raise ValueError("Proposito OTP de correo invalido.")
        return normalized


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str = Field(min_length=20)
    new_password: str = Field(alias="newPassword")

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        validate_password_strength(value)
        return value


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(alias="refreshToken", min_length=20)


class LogoutResponse(BaseModel):
    message: str
