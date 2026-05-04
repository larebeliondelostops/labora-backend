from pydantic import BaseModel


class LegalActionPayload(BaseModel):
    action_type: str
