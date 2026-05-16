from datetime import date, timedelta
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
from app.models.case import CaseOwner, LaboraCase
from app.models.consent import LegalDocument, UserConsent
from app.models.document import Document, DocumentPage, DocumentType, DocumentValidation
from app.models.extraction import (
    ContributionGap,
    Employer,
    ExtractionRun,
    LaborPeriod,
    SalaryBase,
)
from app.models.pre_analysis import CaseSignal, MissingDocument, PreAnalysis, PreIssue
from app.models.questionnaire import CaseProfile
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES, calculate_document_hash
from app.services.pre_analysis_ai_provider import PreAnalysisAiOutput, PreAnalysisProviderResult
from app.utils.dates import utc_now
from app.workers.analysis_worker import run_pre_analysis_job


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
    Base.metadata.create_all(engine)

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
    Base.metadata.drop_all(engine)


def test_pre_analysis_queue_run_result_and_reuse(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))
    _create_case_profile(session_factory, UUID(case_id))

    created = client.post(
        f"/api/v1/cases/{case_id}/pre-analysis",
        json={"forceRegenerate": False, "source": "user_request"},
        headers=headers,
    )

    assert created.status_code == 202
    payload = created.json()
    assert payload["status"] == "queued"
    pre_analysis_id = payload["preAnalysisId"]

    status_response = client.get(
        f"/api/v1/cases/{case_id}/pre-analysis/status",
        headers=headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "queued"

    db = session_factory()
    try:
        actor = db.get(User, user_id)
        result = run_pre_analysis_job(db, pre_analysis_id=pre_analysis_id, actor=actor)
        assert result["status"] == "completed"
    finally:
        db.close()

    fetched = client.get(f"/api/v1/cases/{case_id}/pre-analysis", headers=headers)
    assert fetched.status_code == 200
    data = fetched.json()
    assert data["status"] == "completed"
    assert data["trafficLight"] == "yellow"
    assert data["viabilityLevel"] == "medium"
    assert data["cta"]["type"] == "unlock_full_analysis"
    assert data["issues"]
    assert data["missingDocuments"]
    assert "retroactivo" not in str(data).lower()
    assert "$" not in str(data)

    reused = client.post(
        f"/api/v1/cases/{case_id}/pre-analysis",
        json={"forceRegenerate": False, "source": "user_request"},
        headers=headers,
    )
    assert reused.status_code == 200
    assert reused.json()["preAnalysisId"] == pre_analysis_id
    assert reused.json()["resultAvailable"] is True

    db = session_factory()
    try:
        assert db.query(PreIssue).count() >= 1
        assert db.query(MissingDocument).count() >= 1
        assert db.query(CaseSignal).count() >= 1
        audit_types = {event.event_type for event in db.query(AuditEvent).all()}
        assert "analisis_preliminar_gratuito.created" in audit_types
        assert "analisis_preliminar_gratuito.queued" in audit_types
        assert "analisis_preliminar_gratuito.completed" in audit_types
    finally:
        db.close()


def test_pre_analysis_requires_owner_consents(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, user_id)
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    response = client.post(
        f"/api/v1/cases/{case_id}/pre-analysis",
        json={},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PRE_ANALYSIS_BLOCKED"
    assert response.json()["error"]["details"]["blockedReason"] == "missing_consent"


def test_pre_analysis_blocks_foreign_user_access(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    other_id, other_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, other_id)
    case_id = _create_case_row(session_factory, owner_id)
    _create_document_row(session_factory, user_id=owner_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    response = client.post(
        f"/api/v1/cases/{case_id}/pre-analysis",
        json={},
        headers=other_headers,
    )

    assert owner_headers != other_headers
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_low_confidence_pre_analysis_requires_review(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    class LowConfidenceProvider:
        provider_name = "fake"
        model = "fake-low-confidence"

        def generate_structured_pre_analysis(self, input_payload):
            output = PreAnalysisAiOutput.model_validate(
                {
                    "preliminaryCaseType": "informacion_insuficiente",
                    "trafficLight": "yellow",
                    "viabilityLevel": "medium",
                    "completionScore": 55,
                    "confidence": 0.55,
                    "limitedSummary": "Hay senales iniciales, pero se requiere revision.",
                    "valueDetectedTitle": "Informacion por validar",
                    "valueDetectedSummary": "Se identifican senales generales sin conclusion final.",
                    "issues": [
                        {
                            "type": "insufficient_information",
                            "severity": "medium",
                            "title": "Informacion insuficiente",
                            "publicSummary": "La confianza preliminar es baja.",
                            "confidence": 0.55,
                            "evidenceRefs": [],
                        }
                    ],
                    "missingDocuments": [],
                    "caseSignals": [],
                }
            )
            return PreAnalysisProviderResult(
                provider="fake",
                model="fake-low-confidence",
                output=output,
                latency_ms=1,
                input_hash="hash",
            )

    monkeypatch.setattr(
        "app.services.pre_analysis_service.pre_analysis_provider_factory",
        lambda: LowConfidenceProvider(),
    )

    created = client.post(f"/api/v1/cases/{case_id}/pre-analysis", json={}, headers=headers)
    pre_analysis_id = created.json()["preAnalysisId"]
    db = session_factory()
    try:
        actor = db.get(User, user_id)
        result = run_pre_analysis_job(db, pre_analysis_id=pre_analysis_id, actor=actor)
        assert result["status"] == "requires_review"
    finally:
        db.close()

    fetched = client.get(f"/api/v1/cases/{case_id}/pre-analysis", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "requires_review"
    assert fetched.json()["trafficLight"] == "gray"
    assert fetched.json()["cta"]["type"] == "wait_review"
    assert {warning["code"] for warning in fetched.json()["warnings"]} >= {
        "PRELIMINARY_ONLY",
        "LOW_CONFIDENCE_REVIEW",
    }


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
            document = (
                db.query(LegalDocument)
                .filter(
                    LegalDocument.type == consent_type,
                    LegalDocument.version == "2026.05.01",
                )
                .one_or_none()
            )
            if document is None:
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


def _create_case_row(session_factory, user_id: UUID) -> str:
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
            holder_birth_date=date(1965, 3, 12),
            holder_email="maria@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            pension_fund_or_entity="Colpensiones",
            situation_type="pensioned_with_doubts",
            status="documents_uploaded",
            current_step="documents_uploaded",
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


def _create_document_row(session_factory, *, user_id: UUID, case_id: UUID) -> str:
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
            sha256_hash="b" * 64,
            status="validated",
            validation_status="completed",
            classification_source="manual",
            is_primary=True,
            is_duplicate=False,
            page_count=2,
            is_password_protected=False,
            is_corrupted=False,
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentValidation(
                document_id=document.id,
                status="completed",
                result="accepted_with_warnings",
                score=Decimal("0.8200"),
                checks={},
                warnings=[{"code": "texto_borroso_en_paginas"}],
                errors=[],
                created_by="system",
                created_at=now,
            )
        )
        db.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                ocr_status="completed",
                text_extracted="historia laboral semanas cotizadas colpensiones empleador publico",
                text_confidence=Decimal("0.9000"),
                quality_score=Decimal("0.8000"),
            )
        )
        db.commit()
        return str(document.id)
    finally:
        db.close()


def _create_extraction_rows(session_factory, case_id: UUID) -> None:
    db = session_factory()
    now = utc_now()
    try:
        run = ExtractionRun(
            case_id=case_id,
            status="completed",
            confirmation_status="user_confirmed",
            source="mixed",
            ai_provider="mock",
            ai_model="mock-labor-extraction-v1",
            document_ids=[],
            confidence_avg=Decimal("0.8400"),
            low_confidence_count=0,
            issues_count=0,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.flush()
        employer = Employer(
            case_id=case_id,
            extraction_run_id=run.id,
            name="Entidad Publica",
            employer_type="public",
            confidence=Decimal("0.8400"),
            status="normalized",
            source="ai",
            created_at=now,
            updated_at=now,
        )
        db.add(employer)
        db.flush()
        db.add(
            LaborPeriod(
                case_id=case_id,
                extraction_run_id=run.id,
                employer_id=employer.id,
                start_date=date(2000, 1, 1),
                end_date=date(2000, 12, 31),
                period_type="reported",
                regime_hint="public",
                weeks_detected=Decimal("52.00"),
                salary_base_detected=Decimal("1000000.00"),
                confidence=Decimal("0.8400"),
                status="normalized",
                source="ai",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            ContributionGap(
                case_id=case_id,
                extraction_run_id=run.id,
                start_date=date(2001, 1, 1),
                end_date=date(2001, 2, 1),
                gap_type="missing_period",
                description="Periodo sin soporte suficiente.",
                severity="medium",
                confidence=Decimal("0.7600"),
                status="normalized",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            SalaryBase(
                case_id=case_id,
                extraction_run_id=run.id,
                employer_id=employer.id,
                period_year=2000,
                period_month=1,
                amount=Decimal("1000000.00"),
                currency="COP",
                confidence=Decimal("0.8200"),
                status="normalized",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


def _create_case_profile(session_factory, case_id: UUID) -> None:
    db = session_factory()
    try:
        db.add(
            CaseProfile(
                case_id=case_id,
                has_public_sector_work=True,
                has_teacher_history=False,
                has_special_regime_signal=False,
                has_missing_weeks_claim=True,
                has_reliquidation_signal=False,
                has_prior_claim=True,
                critical_facts=[],
                missing_documents=[],
                confidence=Decimal("0.8000"),
                requires_review=False,
            )
        )
        db.commit()
    finally:
        db.close()
