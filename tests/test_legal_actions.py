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
)
from app.models.legal_action import DraftExport, DraftVersion, LegalAction, LegalDraft
from app.models.report import Report, ReportSection
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES
from app.utils.dates import utc_now


def test_available_actions_recommend_claim_and_block_lawsuit(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _accept_required_consents(session_factory, owner_id)
    analysis_id = _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))
    _create_report_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id), analysis_id=UUID(analysis_id))

    available = client.get(f"/api/v1/cases/{case_id}/legal-actions/available", headers=owner_headers)
    lawsuit = client.post(
        f"/api/v1/cases/{case_id}/legal-actions",
        json={"actionType": "lawsuit_draft"},
        headers=owner_headers,
    )
    claim = client.post(
        f"/api/v1/cases/{case_id}/legal-actions",
        json={"actionType": "administrative_claim"},
        headers=owner_headers,
    )

    assert available.status_code == 200
    actions = {item["actionType"]: item for item in available.json()["actions"]}
    assert actions["administrative_claim"]["status"] == "recommended"
    assert actions["lawsuit_draft"]["status"] == "blocked"
    assert lawsuit.status_code == 409
    assert lawsuit.json()["error"]["code"] == "LEGAL_ACTION_NOT_ALLOWED_BY_ROUTE"
    assert claim.status_code == 201
    assert claim.json()["actionType"] == "administrative_claim"


def test_legal_draft_template_flow_edit_quality_export(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _accept_required_consents(session_factory, owner_id)
    analysis_id = _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))
    _create_report_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id), analysis_id=UUID(analysis_id))
    action_id = client.post(
        f"/api/v1/cases/{case_id}/legal-actions",
        json={"actionType": "administrative_claim"},
        headers=owner_headers,
    ).json()["id"]

    created = client.post(
        f"/api/v1/legal-actions/{action_id}/drafts",
        json={
            "generationMode": "template_only",
            "userInputs": {
                "city": "Medellin",
                "recipient": "Colpensiones",
                "notificationAddress": "maria@example.com",
            },
        },
        headers=owner_headers,
    )
    draft_id = created.json()["draftId"]
    detail = client.get(f"/api/v1/drafts/{draft_id}", headers=owner_headers).json()
    facts_section = next(item for item in detail["sections"] if item["sectionKey"] == "facts")

    edited = client.patch(
        f"/api/v1/drafts/{draft_id}",
        json={
            "sections": [
                {
                    "sectionId": facts_section["id"],
                    "contentHtml": "<p>Hecho editado por usuario.</p><script>alert('x')</script>",
                }
            ],
            "changeSummary": "Ajuste de hechos.",
        },
        headers=owner_headers,
    )
    checked = client.post(f"/api/v1/drafts/{draft_id}/quality-check", headers=owner_headers)
    exported = client.post(
        f"/api/v1/drafts/{draft_id}/export",
        json={"format": "pdf", "includeWatermark": True},
        headers=owner_headers,
    )
    export_id = exported.json()["exportId"]
    download = client.get(f"/api/v1/draft-exports/{export_id}/download", headers=owner_headers)
    parsed = urlsplit(download.json()["downloadUrl"])
    streamed = client.get(f"{parsed.path}?{parsed.query}")
    fetched = client.get(f"/api/v1/drafts/{draft_id}", headers=owner_headers)

    assert created.status_code == 201
    assert created.json()["status"] == "ready_for_edit"
    assert edited.status_code == 200
    assert edited.json()["versionNumber"] >= 2
    assert checked.status_code == 202
    assert exported.status_code == 202
    assert download.status_code == 200
    assert streamed.status_code == 200
    assert streamed.content.startswith(b"%PDF")
    assert "<script>" not in next(
        item for item in fetched.json()["sections"] if item["sectionKey"] == "facts"
    )["contentHtml"]

    db = session_factory()
    try:
        assert db.query(LegalAction).count() == 1
        assert db.query(LegalDraft).count() == 1
        assert db.query(DraftVersion).count() >= 3
        export = db.get(DraftExport, UUID(export_id))
        assert export is not None
        assert export.status == "ready"
        assert export.checksum_sha256
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "legal_draft.created" in audit_names
        assert "legal_draft.edited" in audit_names
        assert "legal_draft.exported" in audit_names
    finally:
        db.close()


def test_mandatory_review_blocks_final_export_until_admin_approval(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="requires_review")
    _accept_required_consents(session_factory, owner_id)
    analysis_id = _create_full_analysis_snapshot(
        session_factory,
        user_id=owner_id,
        case_id=UUID(case_id),
        confidence=Decimal("50.00"),
    )
    _create_report_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id), analysis_id=UUID(analysis_id))
    action_id = client.post(
        f"/api/v1/cases/{case_id}/legal-actions",
        json={"actionType": "administrative_claim"},
        headers=owner_headers,
    ).json()["id"]
    draft_id = client.post(
        f"/api/v1/legal-actions/{action_id}/drafts",
        json={"generationMode": "template_only", "userInputs": {"city": "Bogota"}},
        headers=owner_headers,
    ).json()["draftId"]

    blocked = client.post(
        f"/api/v1/drafts/{draft_id}/export",
        json={"format": "docx", "includeWatermark": False},
        headers=owner_headers,
    )
    submitted = client.post(
        f"/api/v1/drafts/{draft_id}/submit-review",
        json={"message": "Solicito revision profesional.", "priority": "normal"},
        headers=owner_headers,
    )
    listed = client.get("/api/v1/admin/legal-drafts?status=requires_review", headers=admin_headers)
    approved = client.post(
        f"/api/v1/admin/legal-drafts/{draft_id}/review-decision",
        json={"decision": "approved", "reviewNotes": "Aprobado para exportacion."},
        headers=admin_headers,
    )
    exported = client.post(
        f"/api/v1/drafts/{draft_id}/export",
        json={"format": "docx", "includeWatermark": False},
        headers=owner_headers,
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PROFESSIONAL_REVIEW_REQUIRED"
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "requires_review"
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert exported.status_code == 202


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
        token = create_access_token(str(user.id), {"role": role})
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
            next_best_action="generate_legal_action",
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
                slug=f"{consent_type}-v1-{uuid4().hex[:8]}",
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
    confidence: Decimal = Decimal("82.00"),
) -> str:
    db = session_factory()
    now = utc_now()
    try:
        analysis = FullAnalysis(
            case_id=case_id,
            user_id=user_id,
            status="completed" if confidence >= Decimal("70.00") else "requires_review",
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
        db.add(
            CalculationResult(
                full_analysis_id=analysis.id,
                case_id=case_id,
                calculation_code="ECONOMIC_DIFFERENCE_001",
                calculation_name="Diferencia mensual",
                calculation_version="1.0.0",
                calculation_type="amount",
                input_values={},
                formula_ref="test-formula",
                formula_expression="deterministic",
                source_refs=[{"type": "analysis", "id": str(analysis.id)}],
                result_value=Decimal("1000000.00"),
                result_unit="COP",
                result_detail={},
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
                calculation_refs=[{"type": "calculation", "code": "ECONOMIC_DIFFERENCE_001"}],
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


def _create_report_snapshot(
    session_factory,
    *,
    user_id: UUID,
    case_id: UUID,
    analysis_id: UUID,
) -> str:
    db = session_factory()
    now = utc_now()
    try:
        report = Report(
            case_id=case_id,
            owner_user_id=user_id,
            report_type="full",
            title="Informe completo",
            status="ready",
            language="es-CO",
            visibility="user",
            source_analysis_id=analysis_id,
            requires_human_review=False,
            generated_by="deterministic",
            created_at=now,
            updated_at=now,
        )
        db.add(report)
        db.flush()
        db.add(
            ReportSection(
                report_id=report.id,
                section_key="executive_summary",
                title="Resumen ejecutivo",
                content_markdown="Resumen base del informe.",
                order_index=1,
                status="ready",
                source_refs=[{"type": "analysis", "id": str(analysis_id)}],
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            ReportSection(
                report_id=report.id,
                section_key="conclusions",
                title="Conclusiones",
                content_markdown="Conclusiones verificables del informe.",
                order_index=2,
                status="ready",
                source_refs=[{"type": "analysis", "id": str(analysis_id)}],
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
        return str(report.id)
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
