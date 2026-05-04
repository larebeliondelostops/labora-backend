from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Inconsistency(Base):
    __tablename__ = "inconsistencies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, default="")
    legal_impact: Mapped[str] = mapped_column(Text, default="")
    economic_impact: Mapped[str] = mapped_column(Text, default="")
    missing_documents: Mapped[str] = mapped_column(Text, default="")
    confidence_level: Mapped[str] = mapped_column(String(20), default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
