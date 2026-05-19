from datetime import timedelta
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
from app.models.admin import (
    AdminAuditEvent,
    AdminReviewDecision,
    AdminReviewTask,
    AdminRolePermission,
    AdminUser,
    AiConfidenceAlert,
    Assignment,
    CaseQueueItem,
    InternalNote,
)
from app.models.case import (
    CaseHistoryEvent,
    CaseOwner,
    CaseStatusHistory,
    CaseTag,
    LaboraCase,
)
from app.models.consent import (
    ConsentEvidence,
    ConsentIdempotencyKey,
    LegalDocument,
    UserConsent,
)
from app.models.user import User
from app.services.consent_service import (
    REQUIRED_CONSENT_TYPES,
    calculate_document_hash,
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
    AdminUser.__table__,
    AdminRolePermission.__table__,
    CaseQueueItem.__table__,
    Assignment.__table__,
    InternalNote.__table__,
    AdminAuditEvent.__table__,
    AdminReviewTask.__table__,
    AdminReviewDecision.__table__,
    AiConfidenceAlert.__table__,
]

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


def test_create_list_detail_submit_and_history(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)

    created = client.post("/api/v1/cases", json=_case_payload(), headers=headers)

    assert created.status_code == 201
    created_data = created.json()
    assert created_data["caseNumber"].startswith("CASO-")
    assert created_data["status"] == "created"
    assert created_data["nextBestAction"] == "upload_documents"

    listed = client.get("/api/v1/cases?status=created", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1
    assert listed.json()["data"][0]["holderFullName"] == "Maria Gomez Perez"

    case_id = created_data["id"]
    detail = client.get(f"/api/v1/cases/{case_id}", headers=headers)
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["holder"]["documentNumberMasked"] != "52123456"
    assert "upload_documents" in detail_data["allowedActions"]

    updated = client.patch(
        f"/api/v1/cases/{case_id}",
        json={
            "holder": {"firstName": "Maria Fernanda"},
            "caseTypeRequested": "pension_reliquidation",
        },
        headers=headers,
    )
    assert updated.status_code == 200

    submitted = client.post(f"/api/v1/cases/{case_id}/submit", headers=headers)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "ready_for_documents"
    assert submitted.json()["currentStep"] == "documents_pending"

    history = client.get(f"/api/v1/cases/{case_id}/history", headers=headers)
    assert history.status_code == 200
    event_types = {item["eventType"] for item in history.json()["data"]}
    assert "expediente.created" in event_types
    assert "expediente.updated" in event_types
    assert "expediente.submitted" in event_types

    db = session_factory()
    try:
        assert db.query(CaseStatusHistory).filter(CaseStatusHistory.case_id == UUID(case_id)).count() == 2
        audit_types = {event.event_type for event in db.query(AuditEvent).all()}
        assert "expediente.created" in audit_types
        assert "expediente.viewed" in audit_types
        assert "expediente.submitted" in audit_types
    finally:
        db.close()


def test_create_case_requires_completed_consents(client_and_session) -> None:
    client, session_factory = client_and_session
    _user_id, headers = _create_user(session_factory)

    response = client.post("/api/v1/cases", json=_case_payload(), headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_user_cannot_view_another_users_case(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    _other_id, other_headers = _create_user(session_factory)

    case_id = client.post(
        "/api/v1/cases",
        json=_case_payload(),
        headers=owner_headers,
    ).json()["id"]

    response = client.get(f"/api/v1/cases/{case_id}", headers=other_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CASE_ACCESS_DENIED"

    db = session_factory()
    try:
        assert (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "expediente.access_denied")
            .count()
            == 1
        )
    finally:
        db.close()


def test_admin_can_filter_view_assign_and_tag_cases(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, user_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    admin_id, admin_headers = _create_user(session_factory, role="admin")
    reviewer_id, _reviewer_headers = _create_user(session_factory, role="legal_reviewer")

    case_id = client.post(
        "/api/v1/cases",
        json=_case_payload(),
        headers=user_headers,
    ).json()["id"]

    listed = client.get("/api/v1/admin/cases?q=CASO-&pageSize=50", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1

    detail = client.get(f"/api/v1/admin/cases/{case_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["holderDocumentNumber"] == "52123456"

    assigned = client.post(
        f"/api/v1/admin/cases/{case_id}/assign",
        json={"assigneeUserId": str(reviewer_id), "role": "legal_reviewer"},
        headers=admin_headers,
    )
    assert assigned.status_code == 200

    tagged = client.post(
        f"/api/v1/admin/cases/{case_id}/tags",
        json={"tag": "Docente", "source": "admin"},
        headers=admin_headers,
    )
    assert tagged.status_code == 200
    assert tagged.json()["tag"] == "docente"

    db = session_factory()
    try:
        audit_types = {event.event_type for event in db.query(AuditEvent).all()}
        admin_audit_types = {event.event_type for event in db.query(AdminAuditEvent).all()}
        assert "backoffice_admin.viewed" in admin_audit_types
        assert "admin.case.assigned" in admin_audit_types
        assert "expediente.tag_added" in audit_types
        assert (
            db.query(CaseOwner)
            .filter(
                CaseOwner.case_id == UUID(case_id),
                CaseOwner.user_id == reviewer_id,
                CaseOwner.role == "legal_reviewer",
            )
            .count()
            == 1
        )
        assert admin_id is not None
    finally:
        db.close()


def test_internal_status_and_ai_suggestion(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, user_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    _system_id, system_headers = _create_user(session_factory, role="system")

    case_id = client.post(
        "/api/v1/cases",
        json=_case_payload(),
        headers=user_headers,
    ).json()["id"]

    status_response = client.post(
        f"/api/v1/internal/cases/{case_id}/status",
        json={
            "newStatus": "documents_pending",
            "reason": "Esperando carga documental.",
            "sourceModule": "documents",
            "metadata": {"documentCount": 0},
        },
        headers=system_headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["previousStatus"] == "created"
    assert status_response.json()["newStatus"] == "documents_pending"

    suggestion = client.post(
        f"/api/v1/internal/cases/{case_id}/ai-suggestion",
        json={
            "caseTypeSuggested": "teacher_magisterio_case",
            "confidence": 0.5,
            "tags": ["Docente", "Regimen especial"],
            "source": "initial_questionnaire_ai_v1",
        },
        headers=system_headers,
    )
    assert suggestion.status_code == 200
    assert suggestion.json()["caseTypeRequested"] == "labor_history_analysis"
    assert suggestion.json()["caseTypeSuggested"] == "teacher_magisterio_case"
    assert suggestion.json()["status"] == "documents_pending"
    assert "docente" in suggestion.json()["tags"]


def test_internal_status_blocks_requires_review_without_confirmed_payment(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, _headers = _create_user(session_factory)
    _system_id, system_headers = _create_user(session_factory, role="system")
    case_id = _create_case_row(
        session_factory,
        user_id,
        status="preview_locked",
        current_step="preview_locked",
        next_best_action="unlock_full_analysis",
    )

    response = client.post(
        f"/api/v1/internal/cases/{case_id}/status",
        json={
            "newStatus": "requires_review",
            "reason": "Intento de paso manual sin pago.",
            "sourceModule": "payments",
            "metadata": {"origin": "test"},
        },
        headers=system_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_REQUIRED"


def test_internal_status_allows_requires_review_with_confirmed_payment(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, _headers = _create_user(session_factory)
    _system_id, system_headers = _create_user(session_factory, role="system")
    case_id = _create_case_row(
        session_factory,
        user_id,
        status="payment_approved",
        current_step="payment_approved",
        next_best_action="unlock_full_analysis",
    )

    response = client.post(
        f"/api/v1/internal/cases/{case_id}/status",
        json={
            "newStatus": "requires_review",
            "reason": "Revision legal posterior al pago.",
            "sourceModule": "payments",
            "metadata": {"origin": "test"},
        },
        headers=system_headers,
    )

    assert response.status_code == 200
    assert response.json()["newStatus"] == "requires_review"


def test_async_like_payments_transition_cannot_skip_to_requires_review_without_payment(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, _headers = _create_user(session_factory)
    _system_id, system_headers = _create_user(session_factory, role="system")
    case_id = _create_case_row(
        session_factory,
        user_id,
        status="payment_pending",
        current_step="payment_pending",
        next_best_action="wait_payment_confirmation",
    )

    response = client.post(
        f"/api/v1/internal/cases/{case_id}/status",
        json={
            "newStatus": "requires_review",
            "reason": "Evento asincrono intenta salto.",
            "sourceModule": "payments",
            "metadata": {"webhook": True},
        },
        headers=system_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_REQUIRED"


def test_case_detail_serializes_documents_uploaded_preanalysis_action(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    case_id = _create_case_row(
        session_factory,
        user_id,
        status="documents_uploaded",
        current_step="documents_uploaded",
        next_best_action="none",
    )

    detail = client.get(f"/api/v1/cases/{case_id}", headers=headers)
    listed = client.get("/api/v1/cases", headers=headers)

    assert detail.status_code == 200
    assert detail.json()["status"] == "documents_uploaded"
    assert detail.json()["currentStep"] == "documents_uploaded"
    assert detail.json()["nextBestAction"] == "start_preanalysis"
    assert "start_preanalysis" in detail.json()["allowedActions"]
    assert listed.status_code == 200
    assert listed.json()["data"][0]["nextBestAction"] == "start_preanalysis"
    assert "start_preanalysis" in listed.json()["data"][0]["allowedActions"]


def test_case_detail_serializes_preanalysis_pending_action(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    case_id = _create_case_row(
        session_factory,
        user_id,
        status="preanalysis_pending",
        current_step="preanalysis_pending",
        next_best_action="wait_preanalysis",
    )

    detail = client.get(f"/api/v1/cases/{case_id}", headers=headers)

    assert detail.status_code == 200
    assert detail.json()["status"] == "preanalysis_pending"
    assert detail.json()["currentStep"] == "preanalysis_pending"
    assert detail.json()["nextBestAction"] == "start_preanalysis"
    assert "start_preanalysis" in detail.json()["allowedActions"]


def test_case_detail_serializes_preanalysis_ready_action(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    case_id = _create_case_row(
        session_factory,
        user_id,
        status="preanalysis_ready",
        current_step="preanalysis_ready",
        next_best_action="none",
    )

    detail = client.get(f"/api/v1/cases/{case_id}", headers=headers)

    assert detail.status_code == 200
    assert detail.json()["status"] == "preanalysis_ready"
    assert detail.json()["nextBestAction"] == "view_preanalysis"
    assert "view_preanalysis" in detail.json()["allowedActions"]


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


def _create_case_row(
    session_factory,
    user_id: UUID,
    *,
    status: str,
    current_step: str,
    next_best_action: str,
) -> str:
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
            current_step=current_step,
            next_best_action=next_best_action,
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


def _case_payload() -> dict:
    return {
        "holderType": "self",
        "holder": {
            "firstName": "Maria",
            "lastName": "Gomez Perez",
            "documentType": "CC",
            "documentNumber": "52123456",
            "birthDate": "1965-03-12",
            "email": "maria@example.com",
            "phone": "+573001112233",
        },
        "actingAsThirdParty": False,
        "thirdPartyRelationship": None,
        "caseTypeRequested": "labor_history_analysis",
        "pensionFundOrEntity": "Colpensiones",
        "situationType": "pensioned_with_doubts",
    }
