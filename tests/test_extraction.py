from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import app.models  # noqa: F401
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.case import CaseHistoryEvent, CaseOwner, CaseStatusHistory, CaseTag, LaboraCase
from app.models.consent import ConsentEvidence, ConsentIdempotencyKey, LegalDocument, UserConsent
from app.models.document import Document, DocumentPage, DocumentType
from app.models.extraction import (
    ContributionGap,
    ContributionWeek,
    Employer,
    ExtractionAuditEvent,
    ExtractionConfirmation,
    ExtractionField,
    ExtractionIssue,
    ExtractionJob,
    ExtractionRun,
    LaborNovelty,
    LaborPeriod,
    SalaryBase,
    UserCorrection,
)
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES, calculate_document_hash
from app.services.extraction_normalization import (
    normalize_date_value,
    normalize_money_value,
    status_for_confidence,
)
from app.utils.dates import utc_now


TABLES = [
    User.__table__,
    AuditEvent.__table__,
    LegalDocument.__table__,
    UserConsent.__table__,
    ConsentEvidence.__table__,
    ConsentIdempotencyKey.__table__,
    LaboraCase.__table__,
    CaseOwner.__table__,
    CaseStatusHistory.__table__,
    CaseHistoryEvent.__table__,
    CaseTag.__table__,
    DocumentType.__table__,
    Document.__table__,
    DocumentPage.__table__,
    ExtractionRun.__table__,
    Employer.__table__,
    LaborPeriod.__table__,
    ExtractionField.__table__,
    ContributionWeek.__table__,
    SalaryBase.__table__,
    ContributionGap.__table__,
    LaborNovelty.__table__,
    UserCorrection.__table__,
    ExtractionConfirmation.__table__,
    ExtractionIssue.__table__,
    ExtractionAuditEvent.__table__,
    ExtractionJob.__table__,
]

TITLES = {
    "terms_and_conditions": "Terminos y condiciones",
    "personal_data_processing": "Tratamiento de datos personales",
    "sensitive_data_processing": "Tratamiento de datos sensibles",
    "electronic_means": "Medios electronicos",
    "ai_scope_acknowledgement": "Alcance IA",
}


@pytest.fixture()
def client_and_session(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )
    Base.metadata.create_all(engine, tables=TABLES)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, TestingSessionLocal
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine, tables=list(reversed(TABLES)))


def test_extraction_normalizers() -> None:
    assert normalize_date_value("15/05/2026").isoformat() == "2026-05-15"
    assert normalize_money_value("$1.234.567,89") == Decimal("1234567.89")
    assert status_for_confidence(Decimal("0.40")) == "low_confidence"
    assert status_for_confidence(Decimal("0.90")) == "normalized"


def test_extraction_run_query_correction_confirmation_and_analysis_guard(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="documents_uploaded")
    document_id = _create_document_row(
        session_factory,
        user_id,
        UUID(case_id),
        text="historia laboral semanas cotizadas empleador: Empresa ABC SAS",
    )

    started = client.post(
        f"/api/v1/cases/{case_id}/extraction-runs",
        json={
            "documentIds": [document_id],
            "mode": "initial",
            "aiProvider": "mock",
        },
        headers=headers,
    )

    assert started.status_code == 201
    assert started.json()["status"] == "completed"

    extraction = client.get(f"/api/v1/cases/{case_id}/extraction", headers=headers)
    assert extraction.status_code == 200
    data = extraction.json()
    assert data["summary"]["employersCount"] == 1
    assert data["summary"]["laborPeriodsCount"] == 1
    assert data["canConfirm"] is True
    name_field = next(field for field in data["fields"] if field["fieldKey"] == "name")

    patched = client.patch(
        f"/api/v1/cases/{case_id}/extraction-fields",
        json={
            "updates": [
                {
                    "fieldId": name_field["id"],
                    "entityType": "employer",
                    "entityId": data["employers"][0]["id"],
                    "fieldKey": "name",
                    "newValue": "Empresa ABC S.A.S.",
                    "reason": "Nombre corregido por certificacion laboral.",
                }
            ]
        },
        headers=headers,
    )

    assert patched.status_code == 200
    assert patched.json()["updated"] == 1

    corrections = client.get(
        f"/api/v1/cases/{case_id}/extraction/corrections",
        headers=headers,
    )
    assert corrections.status_code == 200
    assert corrections.json()["pagination"]["total"] == 1
    assert corrections.json()["items"][0]["fieldKey"] == "name"

    confirmed = client.post(
        f"/api/v1/cases/{case_id}/confirm-extraction",
        json={
            "acceptLowConfidenceFields": False,
            "markPendingFields": False,
            "userStatement": "Confirmo que revise los datos extraidos.",
        },
        headers=headers,
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["confirmationStatus"] == "user_confirmed"

    analysis = client.post(f"/api/v1/cases/{case_id}/analysis/start", headers=headers)
    assert analysis.status_code == 200

    db = session_factory()
    try:
        audit_types = {event.event_type for event in db.query(AuditEvent).all()}
        assert "extraction.run.started" in audit_types
        assert "extraction.field.corrected" in audit_types
        assert "extraction.confirmed" in audit_types
        assert db.query(ExtractionAuditEvent).count() >= 3
        employer = db.get(Employer, UUID(data["employers"][0]["id"]))
        assert employer.name == "Empresa ABC S.A.S."
    finally:
        db.close()


def test_extraction_start_requires_consent(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, user_id, status="documents_uploaded")
    document_id = _create_document_row(
        session_factory,
        user_id,
        UUID(case_id),
        text="historia laboral empleador: Empresa ABC SAS",
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/extraction-runs",
        json={"documentIds": [document_id], "mode": "initial", "aiProvider": "mock"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_confirmation_blocks_low_confidence_then_allows_pending_fields(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="documents_uploaded")
    run_id, field_id = _create_low_confidence_extraction(session_factory, UUID(case_id))

    analysis = client.post(f"/api/v1/cases/{case_id}/analysis/start", headers=headers)
    assert analysis.status_code == 409
    assert analysis.json()["error"]["code"] == "CONFIRMATION_BLOCKED"

    blocked = client.post(
        f"/api/v1/cases/{case_id}/confirm-extraction",
        json={
            "acceptLowConfidenceFields": False,
            "markPendingFields": False,
            "userStatement": "Revise los datos.",
        },
        headers=headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "LOW_CONFIDENCE_FIELDS_PENDING"

    confirmed = client.post(
        f"/api/v1/cases/{case_id}/confirm-extraction",
        json={
            "acceptLowConfidenceFields": False,
            "markPendingFields": True,
            "userStatement": "Marco como pendiente lo que no puedo validar.",
        },
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmationStatus"] == "confirmed_with_pending_fields"

    db = session_factory()
    try:
        run = db.get(ExtractionRun, run_id)
        field = db.get(ExtractionField, field_id)
        assert run.confirmation_status == "confirmed_with_pending_fields"
        assert field.status == "pending_user_confirmation"
    finally:
        db.close()


def _create_user(session_factory, *, role: str = "user"):
    db = session_factory()
    try:
        user = User(
            email=f"{uuid4()}@example.com",
            first_name="Ana",
            last_name="Gomez",
            full_name="Ana Gomez",
            role=role,
            is_active=True,
            is_verified=True,
            status="active",
            email_verified_at=utc_now(),
        )
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id), {"role": role, "sid": str(uuid4())})
        return user.id, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def _grant_required_consents(session_factory, user_id: UUID) -> None:
    db = session_factory()
    now = utc_now()
    try:
        for consent_type in REQUIRED_CONSENT_TYPES:
            title = TITLES[consent_type]
            content = f"# {title}\n\nContenido legal para {consent_type}."
            document = LegalDocument(
                type=consent_type,
                title=title,
                slug=f"{consent_type}-2026.05.01",
                content_markdown=content,
                content_plain_text=None,
                version="2026.05.01",
                hash_sha256=calculate_document_hash(
                    consent_type=consent_type,
                    title=title,
                    version="2026.05.01",
                    content_markdown=content,
                ),
                status="active",
                is_required=True,
                effective_from=now - timedelta(days=1),
            )
            db.add(document)
            db.flush()
            db.add(
                UserConsent(
                    user_id=user_id,
                    legal_document_id=document.id,
                    consent_type=consent_type,
                    document_version=document.version,
                    document_hash_sha256=document.hash_sha256,
                    accepted=True,
                    accepted_at=now,
                    ip_address="127.0.0.1",
                    user_agent="LaboraTest/1.0",
                    locale="es-CO",
                    source="web",
                    evidence_hash_sha256="a" * 64,
                )
            )
        db.commit()
    finally:
        db.close()


def _create_case_row(session_factory, user_id: UUID, *, status: str) -> str:
    db = session_factory()
    now = utc_now()
    try:
        case = LaboraCase(
            case_number=f"CASO-2026-{uuid4().hex[:6]}",
            owner_user_id=user_id,
            holder_type="self",
            holder_first_name="Maria",
            holder_last_name="Gomez Perez",
            holder_document_type="CC",
            holder_document_number="52123456",
            holder_email="maria@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            pension_fund_or_entity="Colpensiones",
            situation_type="pensioned_with_doubts",
            status=status,
            current_step=status,
            next_best_action="start_preanalysis",
            is_sensitive=True,
            created_at=now,
            updated_at=now,
        )
        db.add(case)
        db.flush()
        db.add(
            CaseOwner(
                case_id=case.id,
                user_id=user_id,
                role="owner",
                permissions={"edit_case": True, "view_history": True, "close_case": True},
            )
        )
        db.commit()
        return str(case.id)
    finally:
        db.close()


def _create_document_row(session_factory, user_id: UUID, case_id: UUID, *, text: str) -> str:
    db = session_factory()
    now = utc_now()
    try:
        document_type = DocumentType(
            code="historia_laboral",
            name="Historia laboral",
            description="Documento principal",
            category="principal",
            is_required_for_basic_flow=True,
            is_primary_candidate=True,
            allowed_mime_types=["application/pdf"],
            max_size_mb=50,
            sort_order=10,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(document_type)
        db.flush()
        document = Document(
            case_id=case_id,
            uploaded_by_user_id=user_id,
            document_type_id=document_type.id,
            original_filename="historia-laboral.pdf",
            display_name="Historia laboral",
            mime_type="application/pdf",
            extension="pdf",
            size_bytes=100,
            storage_bucket="documents",
            storage_key=f"{case_id}/historia.pdf",
            status="validated",
            validation_status="completed",
            classification_source="manual",
            is_primary=True,
            is_duplicate=False,
            page_count=1,
            is_password_protected=False,
            is_corrupted=False,
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                width=612,
                height=792,
                ocr_status="completed",
                text_extracted=text,
                text_confidence=Decimal("0.9500"),
                quality_score=Decimal("0.9500"),
            )
        )
        db.commit()
        return str(document.id)
    finally:
        db.close()


def _create_low_confidence_extraction(session_factory, case_id: UUID) -> tuple[UUID, UUID]:
    db = session_factory()
    now = utc_now()
    try:
        run = ExtractionRun(
            case_id=case_id,
            status="requires_review",
            confirmation_status="ai_extracted",
            source="ai",
            ai_provider="mock",
            ai_model="mock",
            document_ids=[],
            confidence_avg=Decimal("0.4000"),
            low_confidence_count=1,
            issues_count=0,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.flush()
        field = ExtractionField(
            case_id=case_id,
            extraction_run_id=run.id,
            entity_type="labor_period",
            entity_id=None,
            field_key="start_date",
            raw_value="01/02/1998",
            normalized_value="1998-02-01",
            display_value="1998-02-01",
            confidence=Decimal("0.4000"),
            status="low_confidence",
            extraction_method="ai",
            needs_review=True,
            created_at=now,
            updated_at=now,
        )
        db.add(field)
        db.commit()
        return run.id, field.id
    finally:
        db.close()
