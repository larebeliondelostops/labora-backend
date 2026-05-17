import hashlib
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
from app.models.case import CaseOwner, CaseStatusHistory, LaboraCase
from app.models.consent import LegalDocument, UserConsent
from app.models.paywall import ConversionEvent, LockedFeature, Paywall, PreviewResult
from app.models.pre_analysis import PreAnalysis, PreIssue
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES, calculate_document_hash
from app.services.epayco_service import epayco_invoice_for_paywall
from app.utils.dates import utc_now


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


def test_owner_can_generate_view_checkout_and_track_paywall(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    created = client.post(f"/api/v1/cases/{case_id}/preview", headers=headers)
    assert created.status_code == 200
    assert created.json()["status"] == "completed"

    viewed = client.get(f"/api/v1/cases/{case_id}/preview", headers=headers)
    assert viewed.status_code == 200
    data = viewed.json()
    assert data["status"] == "completed"
    assert data["isUnlocked"] is False
    assert data["summary"]["limitedText"] == "Detalle reservado para el analisis completo."
    assert data["summary"]["alertLevel"] == "medium"
    assert data["cta"]["target"] == "checkout"
    assert data["cta"]["priceLabel"] == "$150.000 COP"
    assert "technical_report" in {item["key"] for item in data["lockedContent"]["features"]}
    assert "ganara" not in str(data).lower()
    assert "$10" not in str(data)

    paywall = client.get(f"/api/v1/cases/{case_id}/paywall", headers=headers)
    assert paywall.status_code == 200
    assert paywall.json()["status"] == "blocked"
    assert paywall.json()["unlockRequired"] is True

    checkout = client.post(
        f"/api/v1/cases/{case_id}/checkout/session",
        json={
            "source": "preview_paywall",
            "returnUrl": f"https://labora.centralspike.com/app/cases/{case_id}/preview",
        },
        headers=headers,
    )
    assert checkout.status_code == 201
    assert checkout.json()["checkoutUrl"].startswith("http://localhost:3000/app/cases/")

    db = session_factory()
    try:
        case = db.get(LaboraCase, UUID(case_id))
        case.status = "paid_unlocked"
        case.current_step = "analysis_unlocked"
        case.next_best_action = "start_full_analysis"
        db.commit()
    finally:
        db.close()

    unlocked = client.get(f"/api/v1/cases/{case_id}/preview", headers=headers)
    assert unlocked.status_code == 200
    assert unlocked.json()["isUnlocked"] is True
    assert unlocked.json()["cta"]["target"] == "analysis"

    unlocked_paywall = client.get(f"/api/v1/cases/{case_id}/paywall", headers=headers)
    assert unlocked_paywall.status_code == 200
    assert unlocked_paywall.json()["status"] == "completed"
    assert unlocked_paywall.json()["unlockRequired"] is False

    db = session_factory()
    try:
        assert db.query(PreviewResult).count() == 1
        assert db.query(Paywall).count() == 1
        assert db.query(LockedFeature).count() >= 3
        assert db.get(LaboraCase, UUID(case_id)).status == "paid_unlocked"
        event_names = {event.event_name for event in db.query(ConversionEvent).all()}
        assert {"paywall_viewed", "checkout_started"} <= event_names
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "vista_previa_resultado_paywall.created" in audit_names
        assert "vista_previa_resultado_paywall.viewed" in audit_names
        assert db.query(CaseStatusHistory).filter(CaseStatusHistory.case_id == UUID(case_id)).count() >= 1
    finally:
        db.close()


def test_conversion_event_endpoint_validates_case_access(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    other_id, other_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, other_id)
    case_id = _create_case_row(session_factory, owner_id)

    stored = client.post(
        "/api/v1/analytics/conversion-events",
        json={
            "caseId": case_id,
            "eventName": "preview_cta_clicked",
            "source": "web",
            "metadata": {"placement": "main_cta", "route": f"/app/cases/{case_id}/preview"},
        },
        headers=owner_headers,
    )
    assert stored.status_code == 201
    assert stored.json()["stored"] is True

    denied = client.post(
        "/api/v1/analytics/conversion-events",
        json={
            "caseId": case_id,
            "eventName": "locked_feature_clicked",
            "source": "web",
            "metadata": {"feature": "technical_report"},
        },
        headers=other_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "UNAUTHORIZED_CASE_ACCESS"


def test_preview_blocks_foreign_user_missing_consent_and_missing_preanalysis(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    other_id, other_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, other_id)
    case_id = _create_case_row(session_factory, owner_id)
    _create_pre_analysis_row(session_factory, user_id=owner_id, case_id=UUID(case_id))

    foreign = client.get(f"/api/v1/cases/{case_id}/preview", headers=other_headers)
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "UNAUTHORIZED_CASE_ACCESS"

    no_consent_id, no_consent_headers = _create_user(session_factory)
    no_consent_case_id = _create_case_row(session_factory, no_consent_id)
    _create_pre_analysis_row(
        session_factory,
        user_id=no_consent_id,
        case_id=UUID(no_consent_case_id),
    )
    no_consent = client.get(
        f"/api/v1/cases/{no_consent_case_id}/preview",
        headers=no_consent_headers,
    )
    assert no_consent.status_code == 409
    assert no_consent.json()["error"]["code"] == "CONSENT_REQUIRED"

    missing_preanalysis_id, missing_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, missing_preanalysis_id)
    missing_case_id = _create_case_row(session_factory, missing_preanalysis_id)
    missing_preanalysis = client.get(
        f"/api/v1/cases/{missing_case_id}/preview",
        headers=missing_headers,
    )
    assert missing_preanalysis.status_code == 409
    assert missing_preanalysis.json()["error"]["code"] == "PREANALYSIS_REQUIRED"


def test_low_confidence_preview_requires_review_and_admin_can_approve(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        status="requires_review",
        confidence=Decimal("0.5500"),
    )

    viewed = client.get(f"/api/v1/cases/{case_id}/preview", headers=headers)
    assert viewed.status_code == 200
    data = viewed.json()
    assert data["status"] == "requires_review"
    assert data["summary"]["requiresHumanReview"] is True
    assert data["cta"]["target"] == "review"
    assert "LOW_CONFIDENCE_REVIEW" in {warning["code"] for warning in data["warnings"]}

    checkout = client.post(
        f"/api/v1/cases/{case_id}/checkout/session",
        json={"source": "preview_paywall"},
        headers=headers,
    )
    assert checkout.status_code == 423
    assert checkout.json()["error"]["code"] == "REVIEW_REQUIRED"

    listed = client.get("/api/v1/admin/paywall-previews?status=requires_review", headers=admin_headers)
    assert listed.status_code == 200
    preview_id = listed.json()["items"][0]["id"]

    approved = client.post(
        f"/api/v1/admin/paywall-previews/{preview_id}/approve",
        headers=admin_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"
    assert approved.json()["cta"]["target"] == "checkout"

    db = session_factory()
    try:
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "vista_previa_resultado_paywall.approved" in audit_names
        assert db.get(LaboraCase, UUID(case_id)).status == "preview_locked"
    finally:
        db.close()


def test_checkout_uses_epayco_apify_when_configured(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    monkeypatch.setattr("app.services.epayco_service.settings.EPAYCO_PUBLIC_KEY", "public_key")
    monkeypatch.setattr("app.services.epayco_service.settings.EPAYCO_PRIVATE_KEY", "private_key")
    calls = []

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if url.endswith("/login"):
            return FakeResponse({"token": "apify-token"})
        return FakeResponse(
            {
                "success": True,
                "data": {
                    "sessionId": "epayco-session-123",
                    "token": "epayco-session-token",
                },
            }
        )

    monkeypatch.setattr("app.services.epayco_service.requests.post", fake_post)

    checkout = client.post(
        f"/api/v1/cases/{case_id}/checkout/session",
        json={"source": "preview_paywall"},
        headers=headers,
    )

    assert checkout.status_code == 201
    data = checkout.json()
    assert data["provider"] == "epayco"
    assert data["checkoutSessionId"] == "epayco-session-123"
    assert data["providerSessionToken"] == "epayco-session-token"
    assert calls[0]["url"].endswith("/login")
    assert calls[1]["url"].endswith("/payment/session/create")
    assert calls[1]["json"]["checkout_version"] == "2"
    assert calls[1]["json"]["amount"] == 150000.0
    assert calls[1]["json"]["invoice"].startswith("LABORA-")
    assert calls[1]["json"]["confirmation"].endswith("/api/v1/payments/epayco/confirmation")


def test_epayco_confirmation_unlocks_paywall_with_valid_signature(
    client_and_session,
    monkeypatch,
) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))
    created = client.post(f"/api/v1/cases/{case_id}/preview", headers=headers)
    assert created.status_code == 200

    db = session_factory()
    try:
        paywall = db.query(Paywall).one()
        invoice = epayco_invoice_for_paywall(paywall.id)
    finally:
        db.close()

    monkeypatch.setattr("app.services.epayco_service.settings.EPAYCO_P_CUST_ID_CLIENTE", "cust123")
    monkeypatch.setattr("app.services.epayco_service.settings.EPAYCO_P_KEY", "secret")
    signature_payload = [
        "cust123",
        "secret",
        "ref-001",
        "tx-001",
        "150000.00",
        "COP",
    ]
    signature = hashlib.sha256("^".join(signature_payload).encode("utf-8")).hexdigest()

    response = client.post(
        "/api/v1/payments/epayco/confirmation",
        json={
            "x_cust_id_cliente": "cust123",
            "x_ref_payco": "ref-001",
            "x_transaction_id": "tx-001",
            "x_amount": "150000.00",
            "x_currency_code": "COP",
            "x_id_invoice": invoice,
            "x_cod_response": "1",
            "x_response": "Aceptada",
            "x_signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    db = session_factory()
    try:
        case = db.get(LaboraCase, UUID(case_id))
        paywall = db.query(Paywall).one()
        event_names = {event.event_name for event in db.query(ConversionEvent).all()}
        assert case.status == "paid_unlocked"
        assert paywall.status == "completed"
        assert paywall.unlock_required is False
        assert paywall.unlocked_at is not None
        assert {"checkout_returned", "unlock_completed"} <= event_names
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
            existing = (
                db.query(UserConsent)
                .filter(
                    UserConsent.user_id == user_id,
                    UserConsent.legal_document_id == document.id,
                )
                .one_or_none()
            )
            if existing is None:
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
            status="preanalysis_ready",
            current_step="preanalysis_ready",
            next_best_action="view_preanalysis",
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


def _create_pre_analysis_row(
    session_factory,
    *,
    user_id: UUID,
    case_id: UUID,
    status: str = "completed",
    confidence: Decimal = Decimal("0.7800"),
) -> str:
    db = session_factory()
    now = utc_now()
    try:
        item = PreAnalysis(
            case_id=case_id,
            user_id=user_id,
            status=status,
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
            confidence=confidence,
            limited_summary="Podria existir retroactivo de $10 que debe revisarse.",
            value_detected_title="Senales que vale la pena revisar",
            value_detected_summary="El analisis completo puede ayudar a revisar diferencias generales.",
            cta_type="unlock_full_analysis",
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        db.flush()
        db.add(
            PreIssue(
                pre_analysis_id=item.id,
                case_id=case_id,
                issue_type="possible_unrecognized_weeks",
                severity="medium",
                title="Diferencias preliminares",
                public_summary="Se identificaron posibles diferencias entre periodos reportados.",
                locked_detail_available=True,
                evidence_refs=[],
                confidence=confidence,
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
        return str(item.id)
    finally:
        db.close()
