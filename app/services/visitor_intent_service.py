from sqlalchemy.orm import Session

from app.repositories.visitor_intent_repository import VisitorIntentRepository
from app.schemas.public import IntentClassificationCreate, IntentClassificationResponse


class VisitorIntentService:
    def __init__(self, db: Session) -> None:
        self.visitor_intents = VisitorIntentRepository(db)

    def classify(self, payload: IntentClassificationCreate) -> IntentClassificationResponse:
        text = payload.text.lower()
        intent = "general_commercial_question"
        confidence = 0.62

        keyword_rules = [
            ("possible_missing_weeks", 0.84, ["semanas", "colpensiones", "historia laboral"]),
            ("pension_review", 0.82, ["pension", "pensional", "reliquidacion"]),
            ("document_upload_interest", 0.76, ["documento", "archivo", "subir"]),
            ("pricing_question", 0.78, ["precio", "costo", "pagar", "valor"]),
            ("privacy_question", 0.77, ["datos", "privacidad", "seguridad"]),
        ]
        for candidate_intent, candidate_confidence, keywords in keyword_rules:
            if any(keyword in text for keyword in keywords):
                intent = candidate_intent
                confidence = candidate_confidence
                break

        legal_case_markers = ["mi caso", "demanda", "viabilidad", "cuanto me deben", "calcular"]
        requires_human_followup = confidence < 0.70 or any(
            marker in text for marker in legal_case_markers
        )

        self.visitor_intents.create(
            raw_text=payload.text,
            anonymous_id=payload.anonymous_id,
            intent=intent,
            confidence=confidence,
            requires_human_followup=requires_human_followup,
        )

        return IntentClassificationResponse(
            intent=intent,
            confidence=confidence,
            requires_human_followup=requires_human_followup,
            safe_reply=(
                "Podemos orientarte sobre el proceso. Para revisar tu caso "
                "debes crear un expediente."
            ),
        )
