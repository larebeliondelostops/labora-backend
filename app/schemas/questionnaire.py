from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuestionnaireStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    template_code: str = Field(alias="templateCode", default="guided_case_v1", max_length=80)

    @field_validator("template_code")
    @classmethod
    def normalize_template_code(cls, value: str) -> str:
        return value.strip() or "guided_case_v1"


class QuestionnaireAnswerInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question_code: str = Field(alias="questionCode", min_length=1, max_length=100)
    value: Any

    @field_validator("question_code")
    @classmethod
    def normalize_question_code(cls, value: str) -> str:
        return value.strip()


class QuestionnaireAnswersRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str | None = Field(alias="sessionId", default=None)
    answers: list[QuestionnaireAnswerInput] = Field(default_factory=list)
    client_request_id: str | None = Field(alias="clientRequestId", default=None, max_length=120)


class AnswerPatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    value: Any
    change_reason: str | None = Field(alias="changeReason", default=None, max_length=500)


class QuestionnaireSubmitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str | None = Field(alias="sessionId", default=None)
    confirm_accuracy: bool = Field(alias="confirmAccuracy", default=False)


class AdminQuestionnaireReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(pattern="^(completed|requires_review|blocked|error)$")
    reason: str | None = Field(default=None, max_length=1000)
