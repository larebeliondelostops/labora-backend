from pydantic import BaseModel


class QuestionnairePayload(BaseModel):
    answers_json: dict
