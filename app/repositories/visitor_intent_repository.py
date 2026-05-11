from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.visitor_intent import VisitorIntent


class VisitorIntentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        raw_text: str,
        intent: str,
        confidence: float,
        requires_human_followup: bool,
        anonymous_id: str | None = None,
        model_name: str | None = "rule_based_v1",
    ) -> VisitorIntent:
        visitor_intent = VisitorIntent(
            raw_text=raw_text,
            anonymous_id=anonymous_id,
            intent=intent,
            confidence=Decimal(str(round(confidence, 2))),
            model_name=model_name,
            requires_human_followup=requires_human_followup,
        )
        self.db.add(visitor_intent)
        self.db.commit()
        self.db.refresh(visitor_intent)
        return visitor_intent
