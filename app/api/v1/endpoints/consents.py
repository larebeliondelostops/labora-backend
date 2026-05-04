from fastapi import APIRouter

router = APIRouter()


@router.post("")
def create_consent() -> dict[str, str]:
    return {"message": "Consent scaffold ready"}


@router.get("/me")
def get_my_consents() -> list[dict[str, str]]:
    return [{"consent_type": "terms", "accepted": "true"}]


@router.get("/me/status")
def get_consent_status() -> dict[str, bool]:
    return {"complete": False}
