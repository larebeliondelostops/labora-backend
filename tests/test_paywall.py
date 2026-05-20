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
from app.models.payment import Order, Payment, PaymentTransaction, Receipt, UnlockEvent
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
    assert checkout.json()["checkoutUrl"].startswith("https://new-checkout.epayco.co/checkout/")

    db = session_factory()
    try:
        checkout_audit = next(
            event
            for event in db.query(AuditEvent).all()
            if (event.metadata_json or {}).get("action") == "checkout_started"
        )
        assert checkout_audit.metadata_json["epaycoPayload"]["response"] == (
            f"https://labora.centralspike.com/app/cases/{case_id}/payment/return?provider=epayco"
        )
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


def test_low_confidence_preview_allows_checkout_and_admin_can_approve(client_and_session) -> None:
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
    assert data["cta"]["target"] == "checkout"
    assert "LOW_CONFIDENCE_REVIEW" in {warning["code"] for warning in data["warnings"]}

    checkout = client.post(
        f"/api/v1/cases/{case_id}/checkout/session",
        json={"source": "preview_paywall"},
        headers=headers,
    )
    assert checkout.status_code == 201
    assert checkout.json()["checkoutUrl"].startswith("https://new-checkout.epayco.co/checkout/")

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


def test_payment_checkout_allows_preview_requires_review_and_reuses_pending_payment(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        status="requires_review",
        confidence=Decimal("0.5500"),
    )

    order_response = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={
            "productCode": "FULL_ANALYSIS_UNLOCK",
            "returnUrl": f"https://labora.centralspike.com/app/cases/{case_id}/checkout",
        },
        headers=headers,
    )
    assert order_response.status_code == 201
    order = order_response.json()["order"]

    case_checkout = client.post(
        f"/api/v1/cases/{case_id}/checkout/session",
        json={"source": "payment_flow"},
        headers=headers,
    )
    assert case_checkout.status_code == 201
    assert case_checkout.json()["checkoutUrl"].startswith("https://new-checkout.epayco.co/checkout/")
    assert "requiere revision interna" not in case_checkout.text.lower()

    db = session_factory()
    try:
        stored_order = db.get(Order, UUID(order["id"]))
        stored_order.status = "requires_review"
        db.commit()
    finally:
        db.close()

    checkout_payload = {
        "orderId": order["id"],
        "paymentMethod": "CARD",
        "customer": {
            "fullName": "Maria Gomez",
            "email": "maria@example.com",
            "documentType": "CC",
            "documentNumber": "52123456",
            "phone": "3001112233",
        },
    }
    checkout = client.post("/api/v1/payments/checkout", json=checkout_payload, headers=headers)
    assert checkout.status_code == 201
    payment = checkout.json()["payment"]
    assert payment["status"] == "checkout_started"
    assert payment["checkoutUrl"].startswith("https://new-checkout.epayco.co/checkout/")
    assert "requiere revision interna" not in checkout.text.lower()

    db = session_factory()
    try:
        stored_payment = db.get(Payment, UUID(payment["id"]))
        expected_return_url = f"https://labora.centralspike.com/app/cases/{case_id}/payment/return?provider=epayco"
        assert stored_payment.return_url == expected_return_url
        assert stored_payment.raw_provider_payload["checkoutPayload"]["response"] == expected_return_url
    finally:
        db.close()

    resumed_checkout = client.post("/api/v1/payments/checkout", json=checkout_payload, headers=headers)
    assert resumed_checkout.status_code == 409
    assert resumed_checkout.json()["error"]["code"] == "PAYMENT_ALREADY_PENDING"


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
    assert data["checkoutUrl"] == "https://new-checkout.epayco.co/checkout/epayco-session-123"
    assert data["providerSessionToken"] == "epayco-session-token"
    assert calls[0]["url"].endswith("/login")
    assert calls[1]["url"].endswith("/payment/session/create")
    assert calls[1]["json"]["checkout_version"] == "2"
    assert calls[1]["json"]["amount"] == 150000.0
    assert calls[1]["json"]["invoice"].startswith("LABORA-")
    assert calls[1]["json"]["response"] == (
        f"https://labora.centralspike.com/app/cases/{case_id}/payment/return?provider=epayco"
    )
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


def test_payment_order_checkout_webhook_unlocks_idempotently(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    order_response = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={
            "productCode": "FULL_ANALYSIS_UNLOCK",
            "returnUrl": f"https://labora.centralspike.com/casos/{case_id}/pago/retorno",
            "cancelUrl": f"https://labora.centralspike.com/casos/{case_id}/pago/cancelado",
        },
        headers=headers,
    )
    assert order_response.status_code == 201
    order = order_response.json()["order"]
    assert order["status"] == "created"
    assert order["totalAmount"] == 150000

    repeated_order = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    assert repeated_order.status_code == 201
    assert repeated_order.json()["order"]["id"] == order["id"]

    checkout = client.post(
        "/api/v1/payments/checkout",
        json={
            "orderId": order["id"],
            "paymentMethod": "CARD",
            "customer": {
                "fullName": "Maria Gomez",
                "email": "maria@example.com",
                "documentType": "CC",
                "documentNumber": "52123456",
                "phone": "3001112233",
            },
        },
        headers=headers,
    )
    assert checkout.status_code == 201
    payment = checkout.json()["payment"]
    assert payment["status"] == "checkout_started"
    assert payment["checkoutUrl"].startswith("https://new-checkout.epayco.co/checkout/")

    repeated_checkout = client.post(
        "/api/v1/payments/checkout",
        json={
            "orderId": order["id"],
            "paymentMethod": "CARD",
            "customer": {
                "fullName": "Maria Gomez",
                "email": "maria@example.com",
                "documentType": "CC",
                "documentNumber": "52123456",
                "phone": "3001112233",
            },
        },
        headers=headers,
    )
    assert repeated_checkout.status_code == 409
    assert repeated_checkout.json()["error"]["code"] == "PAYMENT_ALREADY_PENDING"

    pending_flow = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert pending_flow.status_code == 200
    pending_data = pending_flow.json()["paymentFlow"]
    assert pending_data["caseStatus"] == "payment_pending"
    assert pending_data["canPay"] is False
    assert pending_data["canRetry"] is False
    assert pending_data["canContinue"] is False
    assert pending_data["isUnlocked"] is False
    assert pending_data["payment"]["status"] == "checkout_started"
    assert pending_data["payment"]["checkoutUrl"] is None

    db = session_factory()
    try:
        stored_order = db.get(Order, UUID(order["id"]))
        stored_pending_payment = db.get(Payment, UUID(payment["id"]))
        invoice = stored_pending_payment.raw_provider_payload["checkoutPayload"]["invoice"]
        assert invoice.startswith(f"{epayco_invoice_for_paywall(stored_order.paywall_id)}-")
        assert stored_pending_payment.raw_provider_payload["checkoutPayload"]["confirmation"].endswith(
            "/api/v1/payments/webhook/epayco"
        )
        assert stored_pending_payment.raw_provider_payload["checkoutPayload"]["response"] == (
            f"https://labora.centralspike.com/app/cases/{case_id}/payment/return?provider=epayco"
        )
        assert stored_pending_payment.raw_provider_payload["customer"] == {
            "fullName": "Maria Gomez",
            "email": "maria@example.com",
            "documentType": "CC",
            "documentNumber": "52123456",
            "phone": "3001112233",
        }
        assert stored_pending_payment.raw_provider_payload["customerMasked"]["documentNumberLast4"] == "3456"
    finally:
        db.close()

    webhook_payload = {
        "x_ref_payco": "ref-payment-module-001",
        "x_transaction_id": "tx-payment-module-001",
        "x_amount": "150000.00",
        "x_currency_code": "COP",
        "x_id_invoice": invoice,
        "x_cod_response": "1",
        "x_response": "Aceptada",
    }
    webhook = client.post("/api/v1/payments/webhook/epayco", json=webhook_payload)
    assert webhook.status_code == 200
    assert webhook.json()["received"] is True
    assert webhook.json()["duplicate"] is False

    duplicate = client.post("/api/v1/payments/webhook/epayco", json=webhook_payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    status_response = client.get(f"/api/v1/payments/{payment['id']}", headers=headers)
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["payment"]["status"] == "approved"
    assert status_data["case"]["paymentStatus"] == "full_analysis_unlocked"
    assert status_data["case"]["unlockStatus"] == "full_analysis_unlocked"
    assert status_data["receipt"]["available"] is True

    unlocked_flow = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert unlocked_flow.status_code == 200
    unlocked_data = unlocked_flow.json()["paymentFlow"]
    assert unlocked_data["caseStatus"] == "full_analysis_unlocked"
    assert unlocked_data["isUnlocked"] is True
    assert unlocked_data["canContinue"] is True
    assert unlocked_data["canPay"] is False

    receipt = client.get(f"/api/v1/orders/{order['id']}/receipt", headers=headers)
    assert receipt.status_code == 200
    assert receipt.json()["receipt"]["totalAmount"] == 150000

    db = session_factory()
    try:
        case = db.get(LaboraCase, UUID(case_id))
        stored_payment = db.get(Payment, UUID(payment["id"]))
        assert case.status == "full_analysis_unlocked"
        assert db.get(Order, UUID(order["id"])).status == "paid"
        assert stored_payment.status == "approved"
        assert db.query(PaymentTransaction).count() == 1
        assert db.query(UnlockEvent).count() == 1
        assert db.query(Receipt).count() == 1
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert "pago_desbloqueo.approved" in audit_names
        assert "pago_desbloqueo.unlocked" in audit_names
    finally:
        db.close()


def test_payment_flow_reconciles_epayco_return_reference(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    order_response = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    order = order_response.json()["order"]
    checkout = client.post(
        "/api/v1/payments/checkout",
        json={
            "orderId": order["id"],
            "paymentMethod": "CARD",
            "customer": {
                "fullName": "Maria Gomez",
                "email": "maria@example.com",
                "documentType": "CC",
                "documentNumber": "52123456",
                "phone": "3001112233",
            },
        },
        headers=headers,
    )
    payment = checkout.json()["payment"]

    db = session_factory()
    try:
        stored_payment = db.get(Payment, UUID(payment["id"]))
        invoice = stored_payment.raw_provider_payload["checkoutPayload"]["invoice"]
    finally:
        db.close()

    def fake_reference(_self, ref_payco):
        assert ref_payco == "ref-return-001"
        return {
            "success": True,
            "data": {
                "x_ref_payco": "ref-return-001",
                "x_transaction_id": "tx-return-001",
                "x_amount": "150000.00",
                "x_currency_code": "COP",
                "x_id_invoice": invoice,
                "x_cod_response": "1",
                "x_response": "Aceptada",
            },
        }

    monkeypatch.setattr(
        "app.services.payment_service.EpaycoReferenceClient.get_reference",
        fake_reference,
    )

    flow_response = client.get(
        f"/api/v1/cases/{case_id}/payment-flow?provider=epayco&ref_payco=ref-return-001",
        headers=headers,
    )

    assert flow_response.status_code == 200
    flow = flow_response.json()["paymentFlow"]
    assert flow["caseStatus"] == "full_analysis_unlocked"
    assert flow["isUnlocked"] is True
    assert flow["canContinue"] is True
    assert flow["canPay"] is False
    assert flow["payment"]["status"] == "approved"
    assert flow["payment"]["refPayco"] == "ref-return-001"

    db = session_factory()
    try:
        case = db.get(LaboraCase, UUID(case_id))
        stored_order = db.get(Order, UUID(order["id"]))
        stored_payment = db.get(Payment, UUID(payment["id"]))
        assert case.status == "full_analysis_unlocked"
        assert stored_order.status == "paid"
        assert stored_payment.status == "approved"
        assert stored_payment.provider_payment_id == "ref-return-001"
        assert stored_payment.provider_status == "Aceptada"
        assert stored_payment.raw_provider_payload["refPayco"] == "ref-return-001"
        assert stored_payment.raw_provider_payload["_epaycoReferenceResponse"]["success"] is True
        assert db.query(PaymentTransaction).count() == 1
        assert db.query(UnlockEvent).count() == 1
        assert db.query(Receipt).count() == 1
    finally:
        db.close()


def test_pending_payment_flow_does_not_reuse_checkout_url(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    order_response = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    order = order_response.json()["order"]
    checkout = client.post(
        "/api/v1/payments/checkout",
        json={
            "orderId": order["id"],
            "paymentMethod": "CARD",
            "customer": {
                "fullName": "Maria Gomez",
                "email": "maria@example.com",
                "documentType": "CC",
                "documentNumber": "52123456",
                "phone": "3001112233",
            },
        },
        headers=headers,
    )
    assert checkout.status_code == 201
    payment = checkout.json()["payment"]

    db = session_factory()
    try:
        stored_payment = db.get(Payment, UUID(payment["id"]))
        invoice = stored_payment.raw_provider_payload["checkoutPayload"]["invoice"]
    finally:
        db.close()

    webhook = client.post(
        "/api/v1/payments/webhook/epayco",
        json={
            "x_ref_payco": "ref-pending-001",
            "x_transaction_id": "tx-pending-001",
            "x_amount": "150000.00",
            "x_currency_code": "COP",
            "x_id_invoice": invoice,
            "x_cod_response": "3",
            "x_response": "Pendiente",
        },
    )
    assert webhook.status_code == 200

    flow = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert flow.status_code == 200
    data = flow.json()["paymentFlow"]
    assert data["caseStatus"] == "payment_pending"
    assert data["canPay"] is False
    assert data["canRetry"] is False
    assert data["payment"]["status"] == "pending"
    assert data["payment"]["checkoutUrl"] is None

    repeated_checkout = client.post(
        "/api/v1/payments/checkout",
        json={
            "orderId": order["id"],
            "paymentMethod": "CARD",
            "customer": {
                "fullName": "Maria Gomez",
                "email": "maria@example.com",
                "documentType": "CC",
                "documentNumber": "52123456",
                "phone": "3001112233",
            },
        },
        headers=headers,
    )
    assert repeated_checkout.status_code == 409
    assert repeated_checkout.json()["error"]["code"] == "PAYMENT_ALREADY_PENDING"


def test_rejected_payment_retry_uses_new_epayco_invoice(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    order_response = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    order = order_response.json()["order"]
    checkout_payload = {
        "orderId": order["id"],
        "paymentMethod": "CARD",
        "customer": {
            "fullName": "Maria Gomez",
            "email": "maria@example.com",
            "documentType": "CC",
            "documentNumber": "52123456",
            "phone": "3001112233",
        },
    }
    checkout = client.post("/api/v1/payments/checkout", json=checkout_payload, headers=headers)
    first_payment = checkout.json()["payment"]

    db = session_factory()
    try:
        first_invoice = db.get(Payment, UUID(first_payment["id"])).raw_provider_payload["checkoutPayload"]["invoice"]
    finally:
        db.close()

    rejected = client.post(
        "/api/v1/payments/webhook/epayco",
        json={
            "x_ref_payco": "ref-rejected-001",
            "x_transaction_id": "tx-rejected-001",
            "x_amount": "150000.00",
            "x_currency_code": "COP",
            "x_id_invoice": first_invoice,
            "x_cod_response": "2",
            "x_response": "Rechazada",
        },
    )
    assert rejected.status_code == 200

    rejected_flow = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert rejected_flow.status_code == 200
    rejected_data = rejected_flow.json()["paymentFlow"]
    assert rejected_data["caseStatus"] == "payment_rejected"
    assert rejected_data["canPay"] is False
    assert rejected_data["canRetry"] is True

    retry = client.post(
        f"/api/v1/orders/{order['id']}/retry-payment",
        json={"paymentMethod": "CARD"},
        headers=headers,
    )
    assert retry.status_code == 201
    retry_payment = retry.json()["payment"]

    db = session_factory()
    try:
        retry_invoice = db.get(Payment, UUID(retry_payment["id"])).raw_provider_payload["checkoutPayload"]["invoice"]
        assert retry_payment["id"] != first_payment["id"]
        assert retry_invoice != first_invoice
        assert retry_invoice.startswith(first_invoice.rsplit("-", 1)[0])
    finally:
        db.close()


def test_payment_flow_exposes_complete_order_contract(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    created_order = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    assert created_order.status_code == 201
    order = created_order.json()["order"]

    flow_response = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert flow_response.status_code == 200
    flow_order = flow_response.json()["paymentFlow"]["order"]

    assert flow_order["id"] == order["id"]
    assert flow_order["subtotalAmount"] == order["subtotalAmount"] == 150000
    assert flow_order["taxAmount"] == order["taxAmount"] == 0
    assert flow_order["discountAmount"] == order["discountAmount"] == 0
    assert flow_order["totalAmount"] == order["totalAmount"] == 150000
    assert flow_order["currency"] == order["currency"] == "COP"
    assert flow_order["productCode"] == order["productCode"] == "FULL_ANALYSIS_UNLOCK"


def test_payment_flow_provisions_order_when_missing_and_keeps_amount_aliases(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    monkeypatch.setattr("app.services.payment_service.settings.FULL_ANALYSIS_UNLOCK_PRICE_COP", 85000)
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    flow_response = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert flow_response.status_code == 200
    flow = flow_response.json()["paymentFlow"]
    flow_order = flow["order"]

    assert flow["canPay"] is True
    assert flow_order is not None
    assert flow_order["subtotalAmount"] == 85000
    assert flow_order["taxAmount"] == 0
    assert flow_order["discountAmount"] == 0
    assert flow_order["totalAmount"] == 85000
    assert flow_order["currency"] == "COP"
    assert flow_order["subtotal_amount"] == 85000
    assert flow_order["subtotal"] == 85000
    assert flow_order["tax_amount"] == 0
    assert flow_order["tax"] == 0
    assert flow_order["total_amount"] == 85000
    assert flow_order["amount"] == 85000
    assert isinstance(flow_order["totalAmount"], int)


def test_checkout_realigns_paywall_amount_and_never_sends_zero(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    order_response = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    assert order_response.status_code == 201
    order = order_response.json()["order"]
    assert order["totalAmount"] == 150000

    db = session_factory()
    try:
        stored_order = db.get(Order, UUID(order["id"]))
        paywall = db.get(Paywall, stored_order.paywall_id)
        paywall.price_amount = Decimal("0")
        paywall.price_currency = ""
        paywall.price_label = None
        db.commit()
    finally:
        db.close()

    checkout = client.post(
        "/api/v1/payments/checkout",
        json={
            "orderId": order["id"],
            "paymentMethod": "CARD",
            "customer": {
                "fullName": "Maria Gomez",
                "email": "maria@example.com",
                "documentType": "CC",
                "documentNumber": "52123456",
                "phone": "3001112233",
            },
        },
        headers=headers,
    )
    assert checkout.status_code == 201
    payment_id = checkout.json()["payment"]["id"]

    db = session_factory()
    try:
        stored_payment = db.get(Payment, UUID(payment_id))
        checkout_payload = stored_payment.raw_provider_payload["checkoutPayload"]
        stored_order = db.get(Order, UUID(order["id"]))
        paywall = db.get(Paywall, stored_order.paywall_id)
        assert checkout_payload["amount"] == 150000.0
        assert checkout_payload["currency"] == "COP"
        assert stored_payment.amount == 150000
        assert int(paywall.price_amount) == 150000
        assert paywall.price_currency == "COP"
    finally:
        db.close()


def test_paywall_checkout_fails_fast_when_price_is_zero(client_and_session) -> None:
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
        paywall.price_amount = Decimal("0")
        paywall.price_currency = "COP"
        db.commit()
    finally:
        db.close()

    checkout = client.post(
        f"/api/v1/cases/{case_id}/checkout/session",
        json={"source": "preview_paywall"},
        headers=headers,
    )
    assert checkout.status_code == 503
    assert checkout.json()["error"]["code"] == "CHECKOUT_UNAVAILABLE"
    assert checkout.json()["error"]["details"]["reason"] == "EPAYCO_INVALID_AMOUNT"


def test_order_is_recreated_when_active_amount_is_zero(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    first = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    assert first.status_code == 201
    first_order = first.json()["order"]
    assert first_order["totalAmount"] == 150000

    db = session_factory()
    try:
        stored_order = db.get(Order, UUID(first_order["id"]))
        stored_order.subtotal_amount = 0
        stored_order.total_amount = 0
        paywall = db.get(Paywall, stored_order.paywall_id)
        paywall.price_amount = Decimal("0")
        paywall.price_currency = ""
        paywall.price_label = None
        db.commit()
    finally:
        db.close()

    recreated = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    assert recreated.status_code == 201
    recreated_order = recreated.json()["order"]
    assert recreated_order["id"] != first_order["id"]
    assert recreated_order["totalAmount"] == 150000
    assert recreated_order["subtotalAmount"] == 150000

    db = session_factory()
    try:
        stale_order = db.get(Order, UUID(first_order["id"]))
        assert stale_order.status == "expired"
    finally:
        db.close()


def test_payment_flow_repairs_zero_amount_order_with_env_price(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    monkeypatch.setattr("app.services.payment_service.settings.FULL_ANALYSIS_UNLOCK_PRICE_COP", 85000)
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    created = client.post(
        f"/api/v1/cases/{case_id}/orders",
        json={"productCode": "FULL_ANALYSIS_UNLOCK"},
        headers=headers,
    )
    assert created.status_code == 201
    original_order_id = created.json()["order"]["id"]

    db = session_factory()
    try:
        stored_order = db.get(Order, UUID(original_order_id))
        stored_order.subtotal_amount = 0
        stored_order.tax_amount = 0
        stored_order.total_amount = 0
        db.commit()
    finally:
        db.close()

    flow_response = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert flow_response.status_code == 200
    flow_order = flow_response.json()["paymentFlow"]["order"]

    assert flow_order["id"] != original_order_id
    assert flow_order["subtotalAmount"] == 85000
    assert flow_order["taxAmount"] == 0
    assert flow_order["totalAmount"] == 85000

    db = session_factory()
    try:
        stale_order = db.get(Order, UUID(original_order_id))
        replacement_order = db.get(Order, UUID(flow_order["id"]))
        assert stale_order.status == "expired"
        assert replacement_order.total_amount == 85000
        assert replacement_order.subtotal_amount == 85000
    finally:
        db.close()


def test_payment_flow_fails_when_unlock_price_config_is_zero(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    monkeypatch.setattr("app.services.payment_service.settings.FULL_ANALYSIS_UNLOCK_PRICE_COP", 0)
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(session_factory, user_id=user_id, case_id=UUID(case_id))

    response = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PAYMENT_PRICE_CONFIG_INVALID"
    assert response.json()["error"]["details"]["envVar"] == "FULL_ANALYSIS_UNLOCK_PRICE_COP"
    assert response.json()["error"]["details"]["rawValue"] == 0


def test_payment_flow_auto_provisions_order_when_preview_requires_review(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    _create_pre_analysis_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        status="completed",
        confidence=Decimal("0.6500"),
    )

    flow_response = client.get(f"/api/v1/cases/{case_id}/payment-flow", headers=headers)
    assert flow_response.status_code == 200
    flow = flow_response.json()["paymentFlow"]
    order = flow["order"]

    assert flow["canPay"] is True
    assert order is not None
    assert order["subtotalAmount"] == 150000
    assert order["taxAmount"] == 0
    assert order["totalAmount"] == 150000
    assert order["currency"] == "COP"


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
