from datetime import datetime

from sqlalchemy.orm import Session

from app.repositories.public_content_repository import FaqRepository, PublicContentRepository
from app.schemas.public import (
    FaqListResponse,
    FaqResponse,
    PublicContentSection,
    PublicHomeResponse,
)


DEFAULT_HOME_SECTIONS = [
    PublicContentSection(
        section_key="hero",
        title="Revision de historia laboral y analisis pensional",
        subtitle=(
            "Sube tu historia laboral y recibe una orientacion inicial antes "
            "del analisis completo."
        ),
        cta_label="Iniciar analisis",
        cta_url="/registro",
    ),
    PublicContentSection(
        section_key="trust",
        title="IA asistida con validacion profesional",
        subtitle=(
            "Labora organiza informacion y senala posibles inconsistencias "
            "sin reemplazar la revision juridica."
        ),
        cta_label="Crear cuenta",
        cta_url="/registro",
    ),
]

DEFAULT_FAQS = [
    FaqResponse(
        id="default-ia",
        question="Labora reemplaza a un abogado?",
        answer=(
            "No. Labora entrega analisis asistido y puede requerir revision "
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
            "orientacion preliminar antes del pago del analisis completo."
        ),
        category="precio",
        sort_order=2,
    ),
]


class PublicContentService:
    LEGAL_NOTICE = (
        "Labora usa IA asistida. El resultado puede requerir validacion "
        "juridica profesional."
    )

    def __init__(self, db: Session) -> None:
        self.contents = PublicContentRepository(db)
        self.faqs = FaqRepository(db)

    def get_home(self) -> PublicHomeResponse:
        records = self.contents.list_active_sections("home")
        sections = [
            PublicContentSection(
                section_key=record.section_key,
                title=record.title,
                subtitle=record.subtitle,
                body=record.body,
                cta_label=record.cta_label,
                cta_url=record.cta_url,
            )
            for record in records
        ]
        if not sections:
            sections = DEFAULT_HOME_SECTIONS

        updated_at = max((record.updated_at for record in records), default=datetime.utcnow())
        return PublicHomeResponse(
            page="home",
            sections=sections,
            legal_notice=self.LEGAL_NOTICE,
            updated_at=updated_at,
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
