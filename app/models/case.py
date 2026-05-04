from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LaboraCase(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    service_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="draft")
    holder_name: Mapped[str] = mapped_column(String(255))
    acting_as: Mapped[str] = mapped_column(String(50), default="self")
    pension_fund: Mapped[str] = mapped_column(String(100), default="")
    case_type: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
