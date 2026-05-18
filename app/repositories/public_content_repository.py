from sqlalchemy.orm import Session

from app.models.faq_item import FaqItem


class FaqRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_active(self, *, category: str | None = None, limit: int = 20) -> list[FaqItem]:
        query = self.db.query(FaqItem).filter(FaqItem.is_active.is_(True))
        if category:
            query = query.filter(FaqItem.category == category)
        return query.order_by(FaqItem.sort_order.asc(), FaqItem.created_at.asc()).limit(limit).all()
