from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import app.models  # noqa: F401
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.case import CaseOwner, LaboraCase
from app.models.consent import LegalDocument, UserConsent
from app.models.full_analysis import (
    AnalysisInconsistency,
    CalculationResult,
    FullAnalysis,
    LegalRuleResult,
    Scenario,
)
from app.models.report import ExportFile, Report, ReportVersion
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES
from app.utils.dates import utc_now


def test_owner_generates_full_report_with_version_and_sections(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _accept_required_consents(session_factory, owner_id)
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))

    generated = client.post(
        f"/api/v1/cases/{case_id}/reports",
        json={
            "reportType": "full",
            "templateKey": "labora_full_report_v1",
            "outputMode": "sync",
        },
        headers=owner_headers,
    )

    assert generated.status_code == 201
    payload = generated.json()
    assert payload["status"] == "ready"
    assert payload["currentVersionId"] is not None

    listed = client.get(f"/api/v1/cases/{case_id}/reports", headers=owner_headers)
    fetched = client.get(f"/api/v1/reports/{payload['reportId']}", headers=owner_headers)
    versions = client.get(f"/api/v1/reports/{payload['reportId']}/versions", headers=owner_headers)

    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1
    assert fetched.status_code == 200
    detail = fetched.json()
    section_keys = {item["sectionKey"] for item in detail["sections"]}
    assert {"executive_summary", "calculation_detail", "inconsistency_matrix", "conclusions"} <= section_keys
    assert detail["traceability"]["contentHash"]
    assert versions.status_code == 200
    assert versions.json()["items"][0]["versionNumber"] == 1

    db = session_factory()
    try:
        assert db.query(Report).count() == 1
        assert db.query(ReportVersion).count() == 1
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "informes.created" in audit_names
        assert "informes.version_created" in audit_names
        assert "informes.viewed" in audit_names
    finally:
        db.close()


def test_report_generation_requires_payment_unlock(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="preview_locked")
    _accept_required_consents(session_factory, owner_id)
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))

    response = client.post(
        f"/api/v1/cases/{case_id}/reports",
        json={"reportType": "executive", "outputMode": "sync"},
        headers=owner_headers,
    )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "REPORT_PAYMENT_REQUIRED"


def test_report_blocks_foreign_user(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _other_id, other_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _accept_required_consents(session_factory, owner_id)
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))
    generated = client.post(
        f"/api/v1/cases/{case_id}/reports",
        json={"reportType": "executive", "outputMode": "sync"},
        headers=owner_headers,
    )

    denied = client.get(f"/api/v1/reports/{generated.json()['reportId']}", headers=other_headers)

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "REPORT_ACCESS_DENIED"


def test_low_confidence_report_requires_review_and_admin_can_approve(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="requires_review")
    _accept_required_consents(session_factory, owner_id)
    _create_full_analysis_snapshot(
        session_factory,
        user_id=owner_id,
        case_id=UUID(case_id),
        status="requires_review",
        confidence=Decimal("62.00"),
    )

    generated = client.post(
        f"/api/v1/cases/{case_id}/reports",
        json={"reportType": "full", "outputMode": "sync"},
        headers=owner_headers,
    )
    report_id = generated.json()["reportId"]
    export_attempt = client.post(
        f"/api/v1/reports/{report_id}/export",
        json={"format": "pdf"},
        headers=owner_headers,
    )
    approved = client.post(
        f"/api/v1/reports/{report_id}/approve",
        json={"reviewNotes": "Informe validado."},
        headers=admin_headers,
    )

    assert generated.status_code == 201
    assert generated.json()["status"] == "requires_review"
    assert export_attempt.status_code == 409
    assert export_attempt.json()["error"]["code"] == "REPORT_LOW_CONFIDENCE_REQUIRES_REVIEW"
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_report_export_pdf_and_signed_download(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _accept_required_consents(session_factory, owner_id)
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))
    generated = client.post(
        f"/api/v1/cases/{case_id}/reports",
        json={"reportType": "full", "outputMode": "sync"},
        headers=owner_headers,
    )
    report_id = generated.json()["reportId"]

    exported = client.post(
        f"/api/v1/reports/{report_id}/export",
        json={"format": "pdf"},
        headers=owner_headers,
    )
    export_file_id = exported.json()["exportFileId"]
    download = client.get(f"/api/v1/exports/{export_file_id}/download", headers=owner_headers)
    parsed = urlsplit(download.json()["downloadUrl"])
    streamed = client.get(f"{parsed.path}?{parsed.query}")

    assert exported.status_code == 202
    assert exported.json()["status"] == "queued"
    assert download.status_code == 200
    assert streamed.status_code == 200
    assert streamed.content.startswith(b"%PDF")

    db = session_factory()
    try:
        export = db.get(ExportFile, UUID(export_file_id))
        assert export is not None
        assert export.status == "ready"
        assert export.checksum_sha256
        assert export.file_size_bytes > 0
    finally:
        db.close()


def _create_user(session_factory, *, role: str = "user"):
    db = session_factory()
    try:
        user = User(
            email=f"{uuid4().hex}@example.com",
            password_hash="hashed",
            first_name="Maria",
            last_name="Gomez",
            full_name="Maria Gomez",
            role=role,
            is_active=True,
            is_verified=True,
            status="active",
        )
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id))
        return user.id, {"Authorization": f"Bearer {token}"}
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
            current_step="completed",
            next_best_action="view_report",
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


def _accept_required_consents(session_factory, user_id: UUID) -> None:
    db = session_factory()
    now = utc_now()
    try:
        for index, consent_type in enumerate(REQUIRED_CONSENT_TYPES, start=1):
            document = LegalDocument(
                type=consent_type,
                title=f"Documento {consent_type}",
                slug=f"{consent_type}-v1",
                content_markdown="Contenido legal.",
                content_plain_text="Contenido legal.",
                version=f"1.0.{index}",
                hash_sha256=("a" * 63) + str(index),
                status="active",
                is_required=True,
                effective_from=now,
                created_at=now,
                updated_at=now,
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
                    user_agent="test",
                    locale="es-CO",
                    source="web",
                    evidence_hash_sha256=("b" * 63) + str(index),
                    created_at=now,
                )
            )
        db.commit()
    finally:
        db.close()


def _create_full_analysis_snapshot(
    session_factory,
    *,
    user_id: UUID,
    case_id: UUID,
    status: str = "completed",
    confidence: Decimal = Decimal("82.00"),
) -> str:
    db = session_factory()
    now = utc_now()
    try:
        analysis = FullAnalysis(
            case_id=case_id,
            user_id=user_id,
            status=status,
            version=1,
            triggered_by_user_id=user_id,
            triggered_by_role="user",
            input_snapshot={},
            summary={"plainLanguageSummary": "Se detectan semanas posiblemente no reconocidas."},
            executive_result={
                "currency": "COP",
                "viabilityLevel": "medium",
                "recommendedRoute": "administrative_claim",
            },
            legal_conclusion="Existen elementos verificables para reclamar primero ante la entidad.",
            recommended_route="administrative_claim",
            viability_level="medium",
            confidence_global=confidence,
            requires_human_review=confidence < Decimal("70.00"),
            human_review_reason="Confianza baja." if confidence < Decimal("70.00") else None,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(analysis)
        db.flush()
        db.add(
            LegalRuleResult(
                full_analysis_id=analysis.id,
                case_id=case_id,
                rule_code="PROCEDURAL_ROUTE_001",
                rule_name="Ruta procedimental recomendada",
                rule_version="1.0.0",
                rule_category="procedural_route",
                input_facts={},
                source_refs=[{"type": "analysis", "id": str(analysis.id)}],
                condition_expression="deterministic route",
                result="warning",
                result_detail={"recommendedRoute": "administrative_claim"},
                explanation="La ruta inicial es reclamar ante la entidad.",
                confidence=confidence,
                requires_review=confidence < Decimal("70.00"),
                created_at=now,
            )
        )
        for code, name, value, unit, confidence_value in [
            ("CORRECT_ESTIMATED_AMOUNT_001", "Mesada estimada", Decimal("1600000.00"), "COP", confidence),
            ("ECONOMIC_DIFFERENCE_001", "Diferencia mensual", Decimal("1000000.00"), "COP", confidence),
            ("RETROACTIVE_ESTIMATE_001", "Retroactivo estimado", Decimal("12000000.00"), "COP", confidence),
        ]:
            db.add(
                CalculationResult(
                    full_analysis_id=analysis.id,
                    case_id=case_id,
                    calculation_code=code,
                    calculation_name=name,
                    calculation_version="1.0.0",
                    calculation_type="amount",
                    input_values={},
                    formula_ref="test-formula",
                    formula_expression="deterministic",
                    source_refs=[{"type": "analysis", "id": str(analysis.id)}],
                    result_value=value,
                    result_unit=unit,
                    result_detail={"isAssumption": code == "RETROACTIVE_ESTIMATE_001"},
                    confidence=confidence_value,
                    created_at=now,
                )
            )
        db.add(
            Scenario(
                full_analysis_id=analysis.id,
                case_id=case_id,
                scenario_type="calculated_correct",
                name="Escenario calculado por Labora",
                description="Escenario reproducible segun reglas base.",
                base_periods=[],
                legal_basis_refs=[],
                calculation_refs=[],
                amount_estimated=Decimal("1600000.00"),
                weeks_estimated=Decimal("1300.00"),
                retroactive_estimated=Decimal("12000000.00"),
                difference_vs_recognized=Decimal("1000000.00"),
                confidence=confidence,
                created_at=now,
            )
        )
        db.add(
            AnalysisInconsistency(
                full_analysis_id=analysis.id,
                case_id=case_id,
                inconsistency_type="missing_weeks",
                severity="medium",
                title="Posible omision de semanas cotizadas",
                description="Se detectaron periodos que conviene contrastar ante la entidad.",
                evidence_refs=[{"type": "document", "id": str(uuid4()), "label": "Historia laboral"}],
                legal_rule_refs=[{"type": "legal_rule", "code": "MISSING_WEEKS_OR_MORA_001"}],
                calculation_refs=[{"type": "calculation", "code": "RETROACTIVE_ESTIMATE_001"}],
                economic_impact_estimated=Decimal("12000000.00"),
                legal_impact="Puede soportar reclamacion administrativa.",
                missing_documents=["Certificacion laboral del empleador"],
                recommended_action="administrative_claim",
                confidence=confidence,
                created_at=now,
            )
        )
        db.commit()
        return str(analysis.id)
    finally:
        db.close()


@pytest.fixture()
def client_and_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
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
