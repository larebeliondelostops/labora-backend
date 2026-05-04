from pydantic import BaseModel


class AnalysisPayload(BaseModel):
    case_id: str
