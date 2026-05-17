import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.utils.dates import utc_now


class PreviewResult(Base):
    __tablename__ = "preview_results"
    __table_args__ = (
        Index("idx_preview_results_case_created", "case_id", "created_at"),
        Index("idx_preview_results_case_status", "case_id", "status"),
        Index("idx_preview_results_user_status", "user_id", "status"),
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
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    summary_title: Mapped[str] = mapped_column(String(180), nullable=False)
    summary_limited: Mapped[str] = mapped_column(Text, nullable=False)
    alert_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completion_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    hidden_value_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    main_finding_teaser: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    requires_human_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    ai_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ai_prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_preanalysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pre_analysis.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    paywalls: Mapped[list["Paywall"]] = relationship(
        back_populates="preview_result",
        cascade="all, delete-orphan",
    )


class Paywall(Base):
    __tablename__ = "paywalls"
    __table_args__ = (
        Index("idx_paywalls_case_status", "case_id", "status"),
        Index("idx_paywalls_user_status", "user_id", "status"),
        Index("idx_paywalls_preview", "preview_result_id"),
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
    preview_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("preview_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="blocked")
    unlock_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unlock_type: Mapped[str] = mapped_column(String(32), nullable=False, default="payment")
    payment_product_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unlocked_by_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
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

    preview_result: Mapped[PreviewResult] = relationship(back_populates="paywalls")
    locked_features: Mapped[list["LockedFeature"]] = relationship(
        back_populates="paywall",
        cascade="all, delete-orphan",
        order_by="LockedFeature.sort_order",
    )


class LockedFeature(Base):
    __tablename__ = "locked_features"
    __table_args__ = (
        Index("idx_locked_features_paywall_order", "paywall_id", "sort_order"),
        Index("idx_locked_features_key", "feature_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    paywall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paywalls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_highlighted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    paywall: Mapped[Paywall] = relationship(back_populates="locked_features")


class ConversionEvent(Base):
    __tablename__ = "conversion_events"
    __table_args__ = (
        Index("idx_conversion_events_case_created", "case_id", "created_at"),
        Index("idx_conversion_events_user_created", "user_id", "created_at"),
        Index("idx_conversion_events_name", "event_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
