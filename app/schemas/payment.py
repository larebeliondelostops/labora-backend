from pydantic import BaseModel


class PaymentPayload(BaseModel):
    case_id: str
    amount: float
