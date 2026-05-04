from pydantic import BaseModel


class ConsentPayload(BaseModel):
    consent_type: str
    accepted: bool
