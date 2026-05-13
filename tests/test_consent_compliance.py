from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit_event import AuditEvent
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
    Base.metadata.drop_all(engine, tables=TABLES)


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


def _create_legal_documents(
    session_factory,
    *,
    types: list[str] | None = None,
    status: str = "active",
    version: str = "2026.05.01",
) -> dict[str, str]:
    db = session_factory()
    now = utc_now()
    document_ids: dict[str, str] = {}
    try:
        for consent_type in types or REQUIRED_CONSENT_TYPES:
            title = TITLES[consent_type]
            content = f"# {title}\n\nContenido legal para {consent_type}."
            document = LegalDocument(
                type=consent_type,
                title=title,
                slug=f"{consent_type}-{version}",
                content_markdown=content,
                content_plain_text=None,
                version=version,
                hash_sha256=calculate_document_hash(
                    consent_type=consent_type,
                    title=title,
                    version=version,
                    content_markdown=content,
                ),
                status=status,
                is_required=True,
                effective_from=now - timedelta(days=1),
            )
            db.add(document)
            db.flush()
            document_ids[consent_type] = str(document.id)
        db.commit()
        return document_ids
    finally:
        db.close()


def _consent_items(document_ids: dict[str, str], types: list[str] | None = None) -> list[dict]:
    return [
        {
            "legalDocumentId": document_ids[consent_type],
            "consentType": consent_type,
            "accepted": True,
        }
        for consent_type in types or REQUIRED_CONSENT_TYPES
    ]


def test_get_current_legal_documents_returns_only_active(client_and_session) -> None:
    client, session_factory = client_and_session
    _user_id, headers = _create_user(session_factory)
    _create_legal_documents(session_factory)
    _create_legal_documents(
        session_factory,
        types=["terms_and_conditions"],
        status="draft",
        version="2026.05.99",
    )

    response = client.get("/api/v1/legal-documents/current", headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 5
    assert {item["type"] for item in data} == set(REQUIRED_CONSENT_TYPES)
    assert all(item["version"] == "2026.05.01" for item in data)


def test_register_all_required_consents_completes_status_and_audits(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    document_ids = _create_legal_documents(session_factory)

    response = client.post(
        "/api/v1/consents",
        json={"items": _consent_items(document_ids), "source": "web", "locale": "es-CO"},
        headers={**headers, "Idempotency-Key": str(uuid4()), "User-Agent": "LaboraTest/1.0"},
    )

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "completed"
    assert response.json()["data"]["canUploadDocuments"] is True

    status_response = client.get("/api/v1/users/me/consents/status", headers=headers)
    assert status_response.json()["data"]["status"] == "completed"

    db = session_factory()
    try:
        assert db.query(UserConsent).filter(UserConsent.user_id == user_id).count() == 5
        event_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "consentimientos_cumplimiento.submitted" in event_names
        assert "consentimientos_cumplimiento.created" in event_names
        assert "consentimientos_cumplimiento.completed" in event_names
    finally:
        db.close()


def test_document_upload_permission_blocks_missing_sensitive_consent(client_and_session) -> None:
    client, session_factory = client_and_session
    _user_id, headers = _create_user(session_factory)
    document_ids = _create_legal_documents(session_factory)

    response = client.post(
        "/api/v1/consents",
        json={
            "items": _consent_items(
                document_ids,
                ["terms_and_conditions", "personal_data_processing"],
            ),
            "source": "web",
        },
        headers=headers,
    )
    assert response.status_code == 201

    permission = client.get(
        "/api/v1/users/me/permissions/document-upload",
        headers=headers,
    ).json()["data"]
    assert permission["allowed"] is False
    assert permission["reason"] == "missing_required_consents"
    assert "sensitive_data_processing" in permission["missingConsentTypes"]


def test_register_rejects_archived_legal_document(client_and_session) -> None:
    client, session_factory = client_and_session
    _user_id, headers = _create_user(session_factory)
    _create_legal_documents(session_factory)
    archived_id = _create_legal_documents(
        session_factory,
        types=["terms_and_conditions"],
        status="archived",
        version="2026.05.02",
    )["terms_and_conditions"]

    response = client.post(
        "/api/v1/consents",
        json={
            "items": [
                {
                    "legalDocumentId": archived_id,
                    "consentType": "terms_and_conditions",
                    "accepted": True,
                }
            ],
            "source": "web",
        },
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "legal_document_not_active"


def test_register_rejects_consent_type_mismatch(client_and_session) -> None:
    client, session_factory = client_and_session
    _user_id, headers = _create_user(session_factory)
    document_ids = _create_legal_documents(session_factory)

    response = client.post(
        "/api/v1/consents",
        json={
            "items": [
                {
                    "legalDocumentId": document_ids["terms_and_conditions"],
                    "consentType": "personal_data_processing",
                    "accepted": True,
                }
            ],
            "source": "web",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "consent_type_mismatch"


def test_idempotency_key_prevents_duplicate_consent_rows(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    document_ids = _create_legal_documents(session_factory)
    request_headers = {**headers, "Idempotency-Key": str(uuid4())}
    payload = {
        "items": _consent_items(document_ids, ["terms_and_conditions"]),
        "source": "web",
    }

    first = client.post("/api/v1/consents", json=payload, headers=request_headers)
    second = client.post("/api/v1/consents", json=payload, headers=request_headers)

    assert first.status_code == 201
    assert second.status_code == 201
    db = session_factory()
    try:
        assert db.query(UserConsent).filter(UserConsent.user_id == user_id).count() == 1
        assert db.query(ConsentIdempotencyKey).count() == 1
    finally:
        db.close()


def test_consent_evidence_captures_ip_user_agent_version_and_hash(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    document_ids = _create_legal_documents(session_factory)

    response = client.post(
        "/api/v1/consents",
        json={
            "items": _consent_items(document_ids, ["terms_and_conditions"]),
            "source": "web",
        },
        headers={
            **headers,
            "User-Agent": "LaboraBrowser/2.0",
            "X-Forwarded-For": "203.0.113.10",
        },
    )

    assert response.status_code == 201
    db = session_factory()
    try:
        consent = db.query(UserConsent).filter(UserConsent.user_id == user_id).one()
        assert consent.ip_address == "203.0.113.10"
        assert consent.user_agent == "LaboraBrowser/2.0"
        assert consent.document_version == "2026.05.01"
        assert len(consent.document_hash_sha256) == 64
        assert len(consent.evidence_hash_sha256) == 64
        assert consent.evidence.acceptance_payload_json["source"] == "web"
    finally:
        db.close()


def test_admin_endpoint_requires_admin_role(client_and_session) -> None:
    client, session_factory = client_and_session
    _user_id, headers = _create_user(session_factory, role="user")

    response = client.post(
        "/api/v1/admin/legal-documents",
        json={
            "type": "terms_and_conditions",
            "title": "Terminos",
            "contentMarkdown": "# Terminos",
            "version": "2026.06.01",
            "status": "draft",
            "isRequired": True,
            "effectiveFrom": utc_now().isoformat(),
        },
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_activating_new_version_preserves_historical_consents(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, user_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    document_ids = _create_legal_documents(session_factory)

    consent_response = client.post(
        "/api/v1/consents",
        json={
            "items": _consent_items(document_ids, ["terms_and_conditions"]),
            "source": "web",
        },
        headers=user_headers,
    )
    assert consent_response.status_code == 201

    created = client.post(
        "/api/v1/admin/legal-documents",
        json={
            "type": "terms_and_conditions",
            "title": "Terminos y condiciones actualizados",
            "contentMarkdown": "# Nuevos terminos",
            "version": "2026.06.01",
            "status": "draft",
            "isRequired": True,
            "effectiveFrom": (utc_now() + timedelta(days=1)).isoformat(),
        },
        headers=admin_headers,
    )
    assert created.status_code == 201
    new_document_id = created.json()["data"]["id"]

    activated = client.post(
        f"/api/v1/admin/legal-documents/{new_document_id}/activate",
        headers=admin_headers,
    )
    assert activated.status_code == 200

    db = session_factory()
    try:
        consent = db.query(UserConsent).filter(UserConsent.user_id == user_id).one()
        old_document = db.get(LegalDocument, consent.legal_document_id)
        assert consent.document_version == "2026.05.01"
        assert old_document.status == "archived"
        assert db.get(LegalDocument, UUID(new_document_id)).status == "active"
    finally:
        db.close()
