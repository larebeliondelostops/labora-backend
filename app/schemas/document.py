from pydantic import BaseModel


class DocumentPayload(BaseModel):
    case_id: str
    document_type: str
