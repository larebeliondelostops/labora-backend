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
from app.models.admin import AdminAuditEvent, AiConfidenceAlert, CaseQueueItem, InternalNote
from app.models.case import LaboraCase
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


def test_backoffice_case_queue_detail_notes_and_status_are_rbac_protected(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, _owner_headers = _create_user(session_factory)
    _plain_id, plain_headers = _create_user(session_factory)
    _manager_id, manager_headers = _create_user(session_factory, role="admin_manager")
    case_id = _create_case_row(session_factory, owner_id)

    denied = client.get("/api/v1/admin/cases", headers=plain_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_PERMISSION_DENIED"

    listed = client.get("/api/v1/admin/cases?limit=10", headers=manager_headers)
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1
    assert listed.json()["data"][0]["caseId"] == case_id

    detail = client.get(f"/api/v1/admin/cases/{case_id}", headers=manager_headers)
    assert detail.status_code == 200
    assert detail.json()["holderDocumentNumber"] == "52123456"
    assert detail.json()["internalNotes"] == []

    note = client.post(
        f"/api/v1/admin/cases/{case_id}/notes",
        json={"noteType": "legal_observation", "body": "Revisar antes de entrega."},
        headers=manager_headers,
    )
    assert note.status_code == 201
    assert note.json()["visibility"] == "internal"

    missing_reason = client.patch(
        f"/api/v1/admin/cases/{case_id}/status",
        json={"adminStatus": "blocked"},
        headers=manager_headers,
    )
    assert missing_reason.status_code == 400
    assert missing_reason.json()["error"]["code"] == "REVIEW_REASON_REQUIRED"

    updated = client.patch(
        f"/api/v1/admin/cases/{case_id}/status",
        json={
            "adminStatus": "requires_review",
            "reason": "Debe revisar calculo.",
            "blocking": True,
        },
        headers=manager_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["adminStatus"] == "requires_review"
    assert updated.json()["blocking"] is True

    db = session_factory()
    try:
        assert db.query(InternalNote).filter(InternalNote.case_id == UUID(case_id)).count() == 1
        queue_item = db.query(CaseQueueItem).filter(CaseQueueItem.case_id == UUID(case_id)).one()
        assert queue_item.admin_status == "requires_review"
        assert queue_item.has_blocking_issue is True
        audit_types = {event.event_type for event in db.query(AdminAuditEvent).all()}
        assert "backoffice_admin.viewed" in audit_types
        assert "admin.note.created" in audit_types
        assert "admin.case.status_changed" in audit_types
    finally:
        db.close()


def test_backoffice_delivery_is_blocked_by_alerts_and_payment_gate(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, _owner_headers = _create_user(session_factory)
    _manager_id, manager_headers = _create_user(session_factory, role="admin_manager")
    _super_id, super_headers = _create_user(session_factory, role="super_admin")
    case_id = _create_case_row(session_factory, owner_id)
    _create_alert(session_factory, UUID(case_id))

    blocked_by_alert = client.patch(
        f"/api/v1/admin/cases/{case_id}/status",
        json={"adminStatus": "approved_for_delivery", "reason": "Listo para entregar."},
        headers=manager_headers,
    )
    assert blocked_by_alert.status_code == 409
    assert blocked_by_alert.json()["error"]["code"] == "BLOCKING_ALERTS_OPEN"

    alert_id = _latest_alert_id(session_factory, UUID(case_id))
    resolved = client.post(
        f"/api/v1/admin/cases/{case_id}/ai-alerts/{alert_id}/resolve",
        json={
            "resolution": "Revisado manualmente.",
            "keepWarningInUserReport": False,
        },
        headers=super_headers,
    )
    assert resolved.status_code == 200
    assert resolved.json()["resolved"] is True

    blocked_by_payment = client.patch(
        f"/api/v1/admin/cases/{case_id}/status",
        json={"adminStatus": "approved_for_delivery", "reason": "Listo para entregar."},
        headers=manager_headers,
    )
    assert blocked_by_payment.status_code == 409
    assert blocked_by_payment.json()["error"]["code"] == "PAYMENT_REQUIRED_FOR_FULL_DELIVERY"

    override = client.post(
        f"/api/v1/admin/cases/{case_id}/override-unlock",
        json={
            "unlockFullAnalysis": True,
            "reason": "Desbloqueo autorizado por gerencia.",
        },
        headers=super_headers,
    )
    assert override.status_code == 200
    assert override.json()["status"] == "full_analysis_unlocked"

    approved = client.patch(
        f"/api/v1/admin/cases/{case_id}/status",
        json={"adminStatus": "approved_for_delivery", "reason": "Pago/desbloqueo verificado."},
        headers=manager_headers,
    )
    assert approved.status_code == 200

    db = session_factory()
    try:
        audit_types = {event.event_type for event in db.query(AdminAuditEvent).all()}
        assert "admin.ai_alert.resolved" in audit_types
        assert "admin.payment.unlock_overridden" in audit_types
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
            holder_email="maria@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            pension_fund_or_entity="Colpensiones",
            situation_type="pensioned_with_doubts",
            status="preview_locked",
            current_step="preview_locked",
            next_best_action="unlock_full_analysis",
            is_sensitive=True,
            created_at=now,
            updated_at=now,
        )
        db.add(case)
        db.commit()
        return str(case.id)
    finally:
        db.close()


def _create_alert(session_factory, case_id: UUID) -> None:
    db = session_factory()
    try:
        db.add(
            AiConfidenceAlert(
                case_id=case_id,
                source="legal_rules_engine",
                severity="critical",
                confidence_score=None,
                title="Baja confianza juridica",
                description="La regla requiere revision manual.",
                recommendation="Escalar a revisor juridico.",
                resolved=False,
                created_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()


def _latest_alert_id(session_factory, case_id: UUID) -> str:
    db = session_factory()
    try:
        alert = db.query(AiConfidenceAlert).filter(AiConfidenceAlert.case_id == case_id).one()
        return str(alert.id)
    finally:
        db.close()
