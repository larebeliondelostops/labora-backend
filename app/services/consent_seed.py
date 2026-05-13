from app.core.database import SessionLocal
from app.models.consent import LegalDocument
from app.services.consent_service import calculate_document_hash
from app.utils.dates import utc_now


INITIAL_LEGAL_DOCUMENTS = [
    {
        "type": "terms_and_conditions",
        "title": "Terminos y condiciones",
        "slug": "terminos-y-condiciones",
        "content_markdown": (
            "# Terminos y condiciones\n\n"
            "Texto base para la version inicial. Reemplazar por redaccion legal definitiva."
        ),
    },
    {
        "type": "personal_data_processing",
        "title": "Politica de tratamiento de datos personales",
        "slug": "tratamiento-datos-personales",
        "content_markdown": (
            "# Tratamiento de datos personales\n\n"
            "Texto base para autorizacion de tratamiento de datos personales."
        ),
    },
    {
        "type": "sensitive_data_processing",
        "title": "Autorizacion de tratamiento de datos sensibles",
        "slug": "tratamiento-datos-sensibles",
        "content_markdown": (
            "# Tratamiento de datos sensibles\n\n"
            "Texto base para datos laborales, pensionales, juridicos y salariales."
        ),
    },
    {
        "type": "electronic_means",
        "title": "Autorizacion de medios electronicos",
        "slug": "medios-electronicos",
        "content_markdown": (
            "# Medios electronicos\n\n"
            "Texto base para comunicaciones y documentos digitales."
        ),
    },
    {
        "type": "ai_scope_acknowledgement",
        "title": "Alcance del analisis asistido por IA",
        "slug": "alcance-ia",
        "content_markdown": (
            "# Alcance del analisis asistido por IA\n\n"
            "Texto base sobre preanalisis, limitaciones y posible revision profesional."
        ),
    },
]


def seed_initial_legal_documents(version: str = "2026.05.01") -> int:
    db = SessionLocal()
    created_count = 0
    try:
        for item in INITIAL_LEGAL_DOCUMENTS:
            exists = (
                db.query(LegalDocument)
                .filter(
                    LegalDocument.type == item["type"],
                    LegalDocument.version == version,
                )
                .one_or_none()
            )
            if exists is not None:
                continue
            document = LegalDocument(
                type=item["type"],
                title=item["title"],
                slug=item["slug"],
                content_markdown=item["content_markdown"],
                content_plain_text=None,
                version=version,
                hash_sha256=calculate_document_hash(
                    consent_type=item["type"],
                    title=item["title"],
                    version=version,
                    content_markdown=item["content_markdown"],
                ),
                status="active",
                is_required=True,
                effective_from=utc_now(),
            )
            db.add(document)
            created_count += 1
        db.commit()
        return created_count
    finally:
        db.close()


if __name__ == "__main__":
    created = seed_initial_legal_documents()
    print(f"Seed de documentos legales completado. Creados: {created}")
