from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.public_content_repository import FaqRepository
from app.schemas.public import (
    FaqListResponse,
    FaqResponse,
    PublicHomeResponse,
)


DEFAULT_FAQS = [
    FaqResponse(
        id="default-ia",
        question="Labora reemplaza a un abogado?",
        answer=(
            "No. Labora entrega análisis asistido y puede requerir revisión "
            "profesional."
        ),
        category="ia",
        sort_order=1,
    ),
    FaqResponse(
        id="default-flow",
        question="Debo pagar antes de subir mis documentos?",
        answer=(
            "No. El flujo de Labora permite iniciar el proceso y recibir una "
            "orientación preliminar antes del pago del análisis completo."
        ),
        category="precio",
        sort_order=2,
    ),
]


class PublicContentService:
    def __init__(self, db: Session) -> None:
        self.faqs = FaqRepository(db)

    def get_home(self) -> PublicHomeResponse:
        return PublicHomeResponse(
            page="home",
            sections=[],
            legal_notice=None,
            updated_at=datetime.utcnow(),
        )

    def list_faqs(self, *, category: str | None = None, limit: int = 20) -> FaqListResponse:
        records = self.faqs.list_active(category=category, limit=limit)
        items = [
            FaqResponse(
                id=str(record.id),
                question=record.question,
                answer=record.answer,
                category=record.category,
                sort_order=record.sort_order,
            )
            for record in records
        ]
        if not items:
            items = [
                item
                for item in DEFAULT_FAQS
                if category is None or item.category == category
            ][:limit]
        return FaqListResponse(items=items)
