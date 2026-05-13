import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.utils.dates import utc_now


class LaboraCase(Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("idx_cases_owner_status", "owner_user_id", "status"),
        Index("idx_cases_case_type", "case_type_requested"),
        Index("idx_cases_situation_type", "situation_type"),
        Index("idx_cases_created_at", "created_at"),
        Index("idx_cases_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
        nullable=False,
    )
    holder_type: Mapped[str] = mapped_column(String(30), nullable=False)
    holder_first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    holder_last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    holder_document_type: Mapped[str] = mapped_column(String(30), nullable=False)
    holder_document_number: Mapped[str] = mapped_column(String(80), nullable=False)
    holder_birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    holder_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    holder_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    acting_as_third_party: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    third_party_relationship: Mapped[str | None] = mapped_column(
        String(60),
        nullable=True,
    )
    third_party_authorization_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="not_required",
    )
    case_type_requested: Mapped[str] = mapped_column(String(80), nullable=False)
    case_type_suggested: Mapped[str | None] = mapped_column(String(80), nullable=True)
    case_type_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )
    pension_fund_or_entity: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
    )
    situation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_step: Mapped[str] = mapped_column(String(80), nullable=False)
    next_best_action: Mapped[str] = mapped_column(String(80), nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    owners: Mapped[list["CaseOwner"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )
    status_history: Mapped[list["CaseStatusHistory"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )
    history_events: Mapped[list["CaseHistoryEvent"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )
    tags: Mapped[list["CaseTag"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
    )


class CaseOwner(Base):
    __tablename__ = "case_owners"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "user_id",
            "role",
            name="uq_case_owner_user_role",
        ),
        Index("idx_case_owners_user_role", "user_id", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    permissions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    case: Mapped[LaboraCase] = relationship(back_populates="owners")


class CaseStatusHistory(Base):
    __tablename__ = "case_status_history"
    __table_args__ = (
        Index("idx_case_status_history_case_created", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    changed_by_role: Mapped[str] = mapped_column(String(50), nullable=False)
    source_module: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    case: Mapped[LaboraCase] = relationship(back_populates="status_history")


class CaseHistoryEvent(Base):
    __tablename__ = "case_history_events"
    __table_args__ = (
        Index("idx_case_history_events_case_created", "case_id", "created_at"),
        Index("idx_case_history_events_visibility", "visibility"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    metadata_json: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    case: Mapped[LaboraCase] = relationship(back_populates="history_events")


class CaseTag(Base):
    __tablename__ = "case_tags"
    __table_args__ = (
        UniqueConstraint("case_id", "tag", name="uq_case_tag"),
        Index("idx_case_tags_tag", "tag"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    case: Mapped[LaboraCase] = relationship(back_populates="tags")
