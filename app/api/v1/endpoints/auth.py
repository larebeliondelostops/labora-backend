from fastapi import APIRouter

from app.core.security import create_access_token, get_password_hash
from app.schemas.user import UserCreate, UserLogin

router = APIRouter()


@router.post("/register")
def register(payload: UserCreate) -> dict[str, str]:
    return {
        "message": "Registration scaffold ready",
        "password_hash_preview": get_password_hash(payload.password)[:20],
    }


@router.post("/login")
def login(payload: UserLogin) -> dict[str, str]:
    return {"access_token": create_access_token(payload.email), "token_type": "bearer"}


@router.post("/verify")
def verify() -> dict[str, str]:
    return {"message": "Verification scaffold ready"}
