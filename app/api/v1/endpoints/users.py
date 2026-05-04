from fastapi import APIRouter

router = APIRouter()


@router.get("/me")
def get_me() -> dict:
    return {
        "id": "demo-user",
        "email": "demo@labora.local",
        "role": "user",
        "is_active": True,
    }


@router.patch("/me")
def update_me() -> dict[str, str]:
    return {"message": "User profile update scaffold ready"}
