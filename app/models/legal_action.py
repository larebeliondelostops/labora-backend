from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LegalAction(Base):
    __tablename__ = "legal_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    action_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="drafted")
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    pdf_path: Mapped[str] = mapped_column(String(255), default="")
    docx_path: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
