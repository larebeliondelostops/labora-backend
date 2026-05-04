from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    consent_type: Mapped[str] = mapped_column(String(50))
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    text_version: Mapped[str] = mapped_column(String(50))
    text_hash: Mapped[str] = mapped_column(String(128))
    ip_address: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(255))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
