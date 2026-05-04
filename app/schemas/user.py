from pydantic import BaseModel, EmailStr, Field


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
