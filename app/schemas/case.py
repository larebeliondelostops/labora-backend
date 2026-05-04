from pydantic import BaseModel


class CasePayload(BaseModel):
    service_type: str
    holder_name: str
