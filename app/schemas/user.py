from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    first_name: str
    last_name: str
    document_type: str
    document_number: str
    phone: str
    email: EmailStr
    password: str = Field(min_length=8)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


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
