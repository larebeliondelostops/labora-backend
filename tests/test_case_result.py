from datetime import date
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
from app.models.case_result import CaseResult, ResultAuditEvent
from app.models.full_analysis import AnalysisInconsistency, CalculationResult, FullAnalysis, LegalRuleResult
from app.models.user import User
from app.utils.dates import utc_now


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


def test_admin_generates_and_owner_reads_case_result(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))

    generated = client.post(
        f"/api/v1/cases/{case_id}/result/generate",
        json={"forceRegenerate": False, "reason": "analysis_completed"},
        headers=admin_headers,
    )

    assert generated.status_code == 202
    assert generated.json()["status"] == "completed"

    fetched = client.get(f"/api/v1/cases/{case_id}/result", headers=owner_headers)

    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["caseId"] == case_id
    assert payload["caseCode"].startswith("CASO-2026-")
    assert payload["isVisibleToUser"] is True
    assert payload["finalViability"]["level"] == "medium"
    assert payload["recommendedRoute"]["routeType"] == "administrative_claim"
    assert payload["recommendedRoute"]["canGenerateLegalAction"] is True
    assert "demanda" not in payload["recommendedRoute"]["title"].lower()
    assert payload["economicEstimate"]["hasEconomicEstimate"] is True
    assert payload["economicEstimate"]["estimatedRetroactiveAmount"] == 12000000.0
    assert "estimados y pueden cambiar" in payload["economicEstimate"]["warnings"][0]
    assert "viability" in {card["key"] for card in payload["cards"]}
    assert payload["audit"]["viewLogged"] is True

    db = session_factory()
    try:
        assert db.query(CaseResult).count() == 1
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        result_audit_names = {event.event_type for event in db.query(ResultAuditEvent).all()}
        assert "resultado_completo.created" in audit_names
        assert "resultado_completo.viewed" in audit_names
        assert "resultado_completo.created" in result_audit_names
    finally:
        db.close()


def test_case_result_blocks_foreign_user(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _other_id, other_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))
    assert client.post(f"/api/v1/cases/{case_id}/result/generate", headers=admin_headers).status_code == 202

    denied = client.get(f"/api/v1/cases/{case_id}/result", headers=other_headers)

    assert owner_headers != other_headers
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN_CASE_ACCESS"


def test_case_result_requires_payment_unlock(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="preview_locked")
    _create_full_analysis_snapshot(session_factory, user_id=owner_id, case_id=UUID(case_id))

    generated = client.post(f"/api/v1/cases/{case_id}/result/generate", headers=admin_headers)
    status_response = client.get(f"/api/v1/cases/{case_id}/result/status", headers=owner_headers)

    assert generated.status_code == 423
    assert generated.json()["error"]["code"] == "PAYMENT_REQUIRED"
    assert status_response.status_code == 200
    assert "PAYMENT_REQUIRED" in {item["code"] for item in status_response.json()["blockers"]}


def test_low_confidence_result_requires_review_until_approved(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _create_full_analysis_snapshot(
        session_factory,
        user_id=owner_id,
        case_id=UUID(case_id),
        confidence=Decimal("45.00"),
        viability_level="low",
    )

    generated = client.post(f"/api/v1/cases/{case_id}/result/generate", headers=admin_headers)
    result_id = generated.json()["resultId"]
    blocked = client.get(f"/api/v1/cases/{case_id}/result", headers=owner_headers)
    status_response = client.get(f"/api/v1/cases/{case_id}/result/status", headers=owner_headers)

    assert generated.status_code == 202
    assert generated.json()["status"] == "requires_review"
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "RESULT_REQUIRES_REVIEW"
    assert "HUMAN_REVIEW_REQUIRED" in {item["code"] for item in status_response.json()["blockers"]}

    approved = client.post(
        f"/api/v1/cases/{case_id}/result/{result_id}/approve",
        json={"comment": "Resultado revisado."},
        headers=admin_headers,
    )
    fetched = client.get(f"/api/v1/cases/{case_id}/result", headers=owner_headers)

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["isVisibleToUser"] is True
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "approved"


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
            current_step="analysis_unlocked",
            next_best_action="view_result",
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


def _create_full_analysis_snapshot(
    session_factory,
    *,
    user_id: UUID,
    case_id: UUID,
    confidence: Decimal = Decimal("82.00"),
    viability_level: str = "high",
) -> str:
    db = session_factory()
    now = utc_now()
    try:
        analysis = FullAnalysis(
            case_id=case_id,
            user_id=user_id,
            status="completed",
            version=1,
            triggered_by_user_id=user_id,
            triggered_by_role="user",
            input_snapshot={},
            summary={"plainLanguageSummary": "Se detectan semanas posiblemente no reconocidas."},
            executive_result={
                "currency": "COP",
                "viabilityLevel": viability_level,
                "recommendedRoute": "administrative_claim",
            },
            legal_conclusion="Existen elementos verificables para reclamar primero ante la entidad.",
            recommended_route="administrative_claim",
            viability_level=viability_level,
            confidence_global=confidence,
            requires_human_review=confidence < Decimal("70.00"),
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
                source_refs=[],
                condition_expression="deterministic route",
                result="warning",
                result_detail={"recommendedRoute": "administrative_claim"},
                explanation="La ruta inicial es reclamar ante la entidad.",
                confidence=Decimal("82.00"),
                requires_review=False,
                created_at=now,
            )
        )
        for code, name, value, unit, confidence_value in [
            ("CORRECT_ESTIMATED_AMOUNT_001", "Mesada estimada", Decimal("1600000.00"), "COP", Decimal("72.00")),
            ("ECONOMIC_DIFFERENCE_001", "Diferencia mensual", Decimal("1000000.00"), "COP", Decimal("78.00")),
            ("RETROACTIVE_ESTIMATE_001", "Retroactivo estimado", Decimal("12000000.00"), "COP", Decimal("62.00")),
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
                    source_refs=[],
                    result_value=value,
                    result_unit=unit,
                    result_detail={"isAssumption": code == "RETROACTIVE_ESTIMATE_001"},
                    confidence=confidence_value,
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
                confidence=Decimal("82.00"),
                created_at=now,
            )
        )
        db.commit()
        return str(analysis.id)
    finally:
        db.close()
