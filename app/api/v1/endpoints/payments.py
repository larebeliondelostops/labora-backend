from fastapi import APIRouter

router = APIRouter()


@router.post("")
def create_payment() -> dict[str, str]:
    return {"message": "Payment scaffold ready", "status": "pending"}


@router.get("/{payment_id}")
def get_payment(payment_id: str) -> dict[str, str]:
    return {"id": payment_id, "status": "pending"}


@router.post("/webhook")
def payment_webhook() -> dict[str, str]:
    return {"message": "Webhook scaffold ready"}
