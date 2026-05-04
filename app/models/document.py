from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    uploaded_by_user_id: Mapped[str] = mapped_column(String(36))
    document_type: Mapped[str] = mapped_column(String(50))
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(String(255))
    sha256_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(30), default="uploaded")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
