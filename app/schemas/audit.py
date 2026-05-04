from pydantic import BaseModel


class AuditPayload(BaseModel):
    action: str
