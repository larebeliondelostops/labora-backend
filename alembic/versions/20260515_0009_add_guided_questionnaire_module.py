"""add guided questionnaire module

Revision ID: 20260515_0009
Revises: 20260514_0008
Create Date: 2026-05-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260515_0009"
down_revision: Union[str, Sequence[str], None] = "20260514_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "questionnaire_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("case_types", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_questionnaire_template_code_version"),
    )
    op.create_index("idx_questionnaire_templates_status", "questionnaire_templates", ["status"], unique=False)

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("section", sa.String(length=80), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["questionnaire_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "code", name="uq_questions_template_code"),
    )
    op.create_index("idx_questions_template_id", "questions", ["template_id"], unique=False)
    op.create_index("idx_questions_template_section", "questions", ["template_id", "section"], unique=False)

    op.create_table(
        "conditional_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=30), nullable=False),
        sa.Column("target_code", sa.String(length=100), nullable=False),
        sa.Column("condition", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["questionnaire_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_conditional_rules_template_id", "conditional_rules", ["template_id"], unique=False)
    op.create_index(
        "idx_conditional_rules_template_target",
        "conditional_rules",
        ["template_id", "target_type", "target_code"],
        unique=False,
    )

    op.create_table(
        "case_questionnaire_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("completion_percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("current_section", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requires_review_reason", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["questionnaire_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_case_questionnaire_sessions_case", "case_questionnaire_sessions", ["case_id"], unique=False)
    op.create_index("idx_case_questionnaire_sessions_status", "case_questionnaire_sessions", ["status"], unique=False)

    op.create_table(
        "question_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_id", "value", name="uq_question_options_question_value"),
    )
    op.create_index("idx_question_options_question", "question_options", ["question_id"], unique=False)

    op.create_table(
        "questionnaire_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_code", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["case_questionnaire_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "question_id", name="uq_questionnaire_answer_session_question"),
    )
    op.create_index("idx_questionnaire_answers_case", "questionnaire_answers", ["case_id"], unique=False)
    op.create_index("idx_questionnaire_answers_session", "questionnaire_answers", ["session_id"], unique=False)
    op.create_index("idx_questionnaire_answers_question_code", "questionnaire_answers", ["question_code"], unique=False)

    op.create_table(
        "case_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(length=40), nullable=True),
        sa.Column("pension_fund", sa.String(length=160), nullable=True),
        sa.Column("current_status", sa.String(length=80), nullable=True),
        sa.Column("has_public_sector_work", sa.Boolean(), nullable=False),
        sa.Column("has_teacher_history", sa.Boolean(), nullable=False),
        sa.Column("has_special_regime_signal", sa.Boolean(), nullable=False),
        sa.Column("has_missing_weeks_claim", sa.Boolean(), nullable=False),
        sa.Column("has_reliquidation_signal", sa.Boolean(), nullable=False),
        sa.Column("has_prior_claim", sa.Boolean(), nullable=False),
        sa.Column("detected_route", sa.String(length=100), nullable=True),
        sa.Column("critical_facts", sa.JSON(), nullable=False),
        sa.Column("missing_documents", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("generated_from_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generated_from_session_id"], ["case_questionnaire_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_case_profiles_case"),
    )
    op.create_index("idx_case_profiles_requires_review", "case_profiles", ["requires_review"], unique=False)

    op.create_table(
        "ai_questionnaire_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["case_questionnaire_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_questionnaire_events_case", "ai_questionnaire_events", ["case_id"], unique=False)
    op.create_index("idx_ai_questionnaire_events_session", "ai_questionnaire_events", ["session_id"], unique=False)
    op.create_index(
        "idx_ai_questionnaire_events_provider",
        "ai_questionnaire_events",
        ["provider", "model"],
        unique=False,
    )

    op.create_table(
        "questionnaire_answer_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("answer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["answer_id"], ["questionnaire_answers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_questionnaire_answer_versions_answer", "questionnaire_answer_versions", ["answer_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_questionnaire_answer_versions_answer", table_name="questionnaire_answer_versions")
    op.drop_table("questionnaire_answer_versions")
    op.drop_index("idx_ai_questionnaire_events_provider", table_name="ai_questionnaire_events")
    op.drop_index("idx_ai_questionnaire_events_session", table_name="ai_questionnaire_events")
    op.drop_index("idx_ai_questionnaire_events_case", table_name="ai_questionnaire_events")
    op.drop_table("ai_questionnaire_events")
    op.drop_index("idx_case_profiles_requires_review", table_name="case_profiles")
    op.drop_table("case_profiles")
    op.drop_index("idx_questionnaire_answers_question_code", table_name="questionnaire_answers")
    op.drop_index("idx_questionnaire_answers_session", table_name="questionnaire_answers")
    op.drop_index("idx_questionnaire_answers_case", table_name="questionnaire_answers")
    op.drop_table("questionnaire_answers")
    op.drop_index("idx_question_options_question", table_name="question_options")
    op.drop_table("question_options")
    op.drop_index("idx_case_questionnaire_sessions_status", table_name="case_questionnaire_sessions")
    op.drop_index("idx_case_questionnaire_sessions_case", table_name="case_questionnaire_sessions")
    op.drop_table("case_questionnaire_sessions")
    op.drop_index("idx_conditional_rules_template_target", table_name="conditional_rules")
    op.drop_index("idx_conditional_rules_template_id", table_name="conditional_rules")
    op.drop_table("conditional_rules")
    op.drop_index("idx_questions_template_section", table_name="questions")
    op.drop_index("idx_questions_template_id", table_name="questions")
    op.drop_table("questions")
    op.drop_index("idx_questionnaire_templates_status", table_name="questionnaire_templates")
    op.drop_table("questionnaire_templates")
