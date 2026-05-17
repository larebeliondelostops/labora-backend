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
    ContributionWeek,
    ContributionGap,
    Employer,
    ExtractionRun,
    LaborPeriod,
    SalaryBase,
)
from app.models.full_analysis import (
    AnalysisInconsistency,
    CalculationResult,
    FullAnalysis,
    LegalRuleResult,
    Scenario,
)
from app.models.pre_analysis import PreAnalysis
from app.models.questionnaire import CaseProfile
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES, calculate_document_hash
from app.utils.dates import utc_now
from app.workers.analysis_worker import run_full_analysis_job


TITLES = {
    "terms_and_conditions": "Terminos y condiciones",
    "personal_data_processing": "Tratamiento de datos personales",
    "sensitive_data_processing": "Tratamiento de datos sensibles",
    "electronic_means": "Medios electronicos",
    "ai_scope_acknowledgement": "Alcance IA",
}


@pytest.fixture()
def client_and_session():
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


def test_full_analysis_runs_and_exposes_results(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="full_analysis_unlocked")
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id), with_gap=True)
    _create_case_profile(session_factory, UUID(case_id), has_missing_weeks_claim=True)
    _create_pre_analysis(session_factory, user_id=user_id, case_id=UUID(case_id))

    created = client.post(
        f"/api/v1/cases/{case_id}/full-analysis",
        json={"forceReprocess": False, "reason": "user_requested"},
        headers=headers,
    )

    assert created.status_code == 202
    full_analysis_id = created.json()["id"]

    db = session_factory()
    try:
        actor = db.get(User, user_id)
        result = run_full_analysis_job(db, full_analysis_id=full_analysis_id, actor=actor)
        assert result["status"] in {"completed", "requires_review"}
    finally:
        db.close()

    fetched = client.get(f"/api/v1/cases/{case_id}/full-analysis", headers=headers)
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["status"] in {"completed", "requires_review"}
    assert payload["progress"]["percentage"] in {90, 100}
    assert payload["executiveResult"]["currency"] == "COP"
    assert payload["confidence"]["globalScore"] is not None

    rules = client.get(f"/api/v1/cases/{case_id}/rules-results", headers=headers)
    assert rules.status_code == 200
    assert {item["ruleCode"] for item in rules.json()["items"]} >= {
        "REGIME_CLASSIFICATION_001",
        "MISSING_WEEKS_OR_MORA_001",
    }

    calculations = client.get(f"/api/v1/cases/{case_id}/calculations", headers=headers)
    assert calculations.status_code == 200
    assert {item["calculationCode"] for item in calculations.json()["items"]} >= {
        "WEEKS_TOTAL_001",
        "MISSING_WEEKS_ESTIMATE_001",
    }
    assert all(item["formulaExpression"] for item in calculations.json()["items"])

    scenarios = client.get(f"/api/v1/cases/{case_id}/full-analysis/scenarios", headers=headers)
    assert scenarios.status_code == 200
    assert {item["scenarioType"] for item in scenarios.json()["items"]} >= {
        "recognized_by_entity",
        "calculated_correct",
    }

    inconsistencies = client.get(
        f"/api/v1/cases/{case_id}/full-analysis/inconsistencies",
        headers=headers,
    )
    assert inconsistencies.status_code == 200
    assert "missing_weeks" in {item["type"] for item in inconsistencies.json()["items"]}

    confidence = client.get(f"/api/v1/cases/{case_id}/full-analysis/confidence", headers=headers)
    assert confidence.status_code == 200
    assert "global" in {item["scope"] for item in confidence.json()["scores"]}

    db = session_factory()
    try:
        assert db.query(FullAnalysis).count() == 1
        assert db.query(LegalRuleResult).count() >= 3
        assert db.query(CalculationResult).count() >= 3
        assert db.query(Scenario).count() >= 2
        assert db.query(AnalysisInconsistency).count() >= 1
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "analisis_completo.created" in audit_names
        assert "analisis_completo.rules_completed" in audit_names
        assert "analisis_completo.confidence_completed" in audit_names
    finally:
        db.close()


def test_full_analysis_requires_payment_unlock(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="preanalysis_ready")
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    response = client.post(f"/api/v1/cases/{case_id}/full-analysis", headers=headers)

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "PAYMENT_REQUIRED"


def test_full_analysis_blocks_foreign_user(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    other_id, other_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, other_id)
    case_id = _create_case_row(session_factory, owner_id, status="full_analysis_unlocked")
    _create_document_row(session_factory, user_id=owner_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    response = client.post(f"/api/v1/cases/{case_id}/full-analysis", headers=other_headers)

    assert owner_headers != other_headers
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CASE_ACCESS_DENIED"


def test_full_analysis_reuses_active_run(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="full_analysis_unlocked")
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    active_id = _create_active_full_analysis(session_factory, user_id=user_id, case_id=UUID(case_id))

    response = client.post(f"/api/v1/cases/{case_id}/full-analysis", headers=headers)

    assert response.status_code == 202
    assert response.json()["id"] == active_id
    db = session_factory()
    try:
        assert db.query(FullAnalysis).count() == 1
    finally:
        db.close()


def test_full_analysis_requires_ready_preanalysis(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="full_analysis_unlocked")
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(session_factory, UUID(case_id))

    response = client.post(f"/api/v1/cases/{case_id}/full-analysis", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CASE_NOT_READY_FOR_FULL_ANALYSIS"
    assert response.json()["error"]["details"]["blockedReason"] == "preanalysis_missing"


def test_full_analysis_uses_contribution_weeks_without_periods(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id, status="full_analysis_unlocked")
    _create_document_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    _create_extraction_rows(
        session_factory,
        UUID(case_id),
        with_period=False,
        contribution_weeks=Decimal("20.50"),
    )
    _create_pre_analysis(session_factory, user_id=user_id, case_id=UUID(case_id))

    created = client.post(f"/api/v1/cases/{case_id}/full-analysis", headers=headers)

    assert created.status_code == 202
    calculations = client.get(f"/api/v1/cases/{case_id}/calculations", headers=headers)
    assert calculations.status_code == 200
    weeks_total = next(
        item
        for item in calculations.json()["items"]
        if item["calculationCode"] == "WEEKS_TOTAL_001"
    )
    assert weeks_total["resultValue"] == 20.5
    assert weeks_total["resultDetail"]["usedSource"] == "contribution_weeks"


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
            holder_birth_date=date(1965, 3, 12),
            holder_email="maria@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            pension_fund_or_entity="Colpensiones",
            situation_type="pensioned_with_doubts",
            status=status,
            current_step="analysis_unlocked" if status == "full_analysis_unlocked" else status,
            next_best_action="start_full_analysis",
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
            code=f"historia_laboral_{uuid4().hex[:8]}",
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
                text_extracted="historia laboral semanas cotizadas colpensiones",
                text_confidence=Decimal("0.9000"),
                quality_score=Decimal("0.8000"),
            )
        )
        db.commit()
        return str(document.id)
    finally:
        db.close()


def _create_extraction_rows(
    session_factory,
    case_id: UUID,
    *,
    with_gap: bool = False,
    with_period: bool = True,
    contribution_weeks: Decimal | None = None,
) -> None:
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
            name="Empresa Privada",
            employer_type="private",
            confidence=Decimal("0.8400"),
            status="normalized",
            source="ai",
            created_at=now,
            updated_at=now,
        )
        db.add(employer)
        db.flush()
        if with_period:
            db.add(
                LaborPeriod(
                    case_id=case_id,
                    extraction_run_id=run.id,
                    employer_id=employer.id,
                    start_date=date(2000, 1, 1),
                    end_date=date(2000, 12, 31),
                    period_type="reported",
                    regime_hint="general",
                    weeks_detected=Decimal("52.00"),
                    salary_base_detected=Decimal("1000000.00"),
                    confidence=Decimal("0.8400"),
                    status="normalized",
                    source="ai",
                    created_at=now,
                    updated_at=now,
                )
            )
        if contribution_weeks is not None:
            db.add(
                ContributionWeek(
                    case_id=case_id,
                    extraction_run_id=run.id,
                    employer_id=employer.id,
                    year=2000,
                    month=1,
                    weeks=contribution_weeks,
                    days=None,
                    source="ai",
                    confidence=Decimal("0.8600"),
                    status="normalized",
                    created_at=now,
                    updated_at=now,
                )
            )
        if with_gap:
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


def _create_case_profile(
    session_factory,
    case_id: UUID,
    *,
    has_missing_weeks_claim: bool = False,
) -> None:
    db = session_factory()
    try:
        db.add(
            CaseProfile(
                case_id=case_id,
                has_public_sector_work=False,
                has_teacher_history=False,
                has_special_regime_signal=False,
                has_missing_weeks_claim=has_missing_weeks_claim,
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


def _create_pre_analysis(session_factory, *, user_id: UUID, case_id: UUID) -> None:
    db = session_factory()
    now = utc_now()
    try:
        db.add(
            PreAnalysis(
                case_id=case_id,
                user_id=user_id,
                status="completed",
                input_hash=uuid4().hex,
                prompt_version="pre-analysis-v1",
                output_schema_version="pre-analysis-output-v1",
                extractor_version="extraction-summary-v1",
                ai_provider="mock",
                ai_model="mock-pre-analysis-v1",
                preliminary_case_type="historia_laboral",
                traffic_light="yellow",
                viability_level="medium",
                completion_score=Decimal("82.00"),
                confidence=Decimal("0.7800"),
                limited_summary="Existen senales generales por revisar.",
                value_detected_title="Senales que vale la pena revisar",
                value_detected_summary="El analisis completo puede revisar diferencias generales.",
                cta_type="unlock_full_analysis",
                completed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


def _create_active_full_analysis(session_factory, *, user_id: UUID, case_id: UUID) -> str:
    db = session_factory()
    now = utc_now()
    try:
        item = FullAnalysis(
            case_id=case_id,
            user_id=user_id,
            status="in_progress",
            version=1,
            triggered_by_user_id=user_id,
            triggered_by_role="user",
            input_snapshot={},
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        db.commit()
        return str(item.id)
    finally:
        db.close()
