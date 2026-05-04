from pydantic import BaseModel


class ReportPayload(BaseModel):
    report_type: str
