import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


class QuestionnaireTemplate(Base):
    __tablename__ = "questionnaire_templates"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_questionnaire_template_code_version"),
        Index("idx_questionnaire_templates_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    case_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
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

    questions: Mapped[list["Question"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="Question.display_order",
    )
    conditional_rules: Mapped[list["ConditionalRule"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="ConditionalRule.priority",
    )


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("template_id", "code", name="uq_questions_template_code"),
        Index("idx_questions_template_section", "template_id", "section"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questionnaire_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    section: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column("order", Integer, nullable=False, default=0)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
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

    template: Mapped[QuestionnaireTemplate] = relationship(back_populates="questions")
    options: Mapped[list["QuestionOption"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOption.display_order",
    )


class QuestionOption(Base):
    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint("question_id", "value", name="uq_question_options_question_value"),
        Index("idx_question_options_question", "question_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    display_order: Mapped[int] = mapped_column("order", Integer, nullable=False, default=0)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    question: Mapped[Question] = relationship(back_populates="options")


class ConditionalRule(Base):
    __tablename__ = "conditional_rules"
    __table_args__ = (
        Index("idx_conditional_rules_template_target", "template_id", "target_type", "target_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questionnaire_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_code: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_json: Mapped[dict] = mapped_column("condition", JSON, nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    template: Mapped[QuestionnaireTemplate] = relationship(back_populates="conditional_rules")


class CaseQuestionnaireSession(Base):
    __tablename__ = "case_questionnaire_sessions"
    __table_args__ = (
        Index("idx_case_questionnaire_sessions_case", "case_id"),
        Index("idx_case_questionnaire_sessions_status", "status"),
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
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questionnaire_templates.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_started")
    completion_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    current_section: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    requires_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
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

    template: Mapped[QuestionnaireTemplate] = relationship()
    answers: Mapped[list["QuestionnaireAnswer"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class QuestionnaireAnswer(Base):
    __tablename__ = "questionnaire_answers"
    __table_args__ = (
        UniqueConstraint("session_id", "question_id", name="uq_questionnaire_answer_session_question"),
        Index("idx_questionnaire_answers_case", "case_id"),
        Index("idx_questionnaire_answers_session", "session_id"),
        Index("idx_questionnaire_answers_question_code", "question_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_questionnaire_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questions.id"),
        nullable=False,
    )
    question_code: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="user")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
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

    session: Mapped[CaseQuestionnaireSession] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship()
    versions: Mapped[list["AnswerVersion"]] = relationship(
        back_populates="answer",
        cascade="all, delete-orphan",
    )


class AnswerVersion(Base):
    __tablename__ = "questionnaire_answer_versions"
    __table_args__ = (
        Index("idx_questionnaire_answer_versions_answer", "answer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questionnaire_answers.id", ondelete="CASCADE"),
        nullable=False,
    )
    previous_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    answer: Mapped[QuestionnaireAnswer] = relationship(back_populates="versions")


class CaseProfile(Base):
    __tablename__ = "case_profiles"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_case_profiles_case"),
        Index("idx_case_profiles_requires_review", "requires_review"),
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
    )
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pension_fund: Mapped[str | None] = mapped_column(String(160), nullable=True)
    current_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    has_public_sector_work: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_teacher_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_special_regime_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_missing_weeks_claim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_reliquidation_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_prior_claim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detected_route: Mapped[str | None] = mapped_column(String(100), nullable=True)
    critical_facts: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    missing_documents: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.0000"))
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_from_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_questionnaire_sessions.id"),
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


class AiQuestionnaireEvent(Base):
    __tablename__ = "ai_questionnaire_events"
    __table_args__ = (
        Index("idx_ai_questionnaire_events_case", "case_id"),
        Index("idx_ai_questionnaire_events_session", "session_id"),
        Index("idx_ai_questionnaire_events_provider", "provider", "model"),
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
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_questionnaire_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
