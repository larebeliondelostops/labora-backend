from fastapi.testclient import TestClient

from app.core.rate_limit import public_rate_limiter
from app.main import app
from app.schemas.public import (
    FaqListResponse,
    FaqResponse,
    LeadCreateResponse,
    NextAction,
    PublicContentSection,
    PublicEventResponse,
    PublicHomeResponse,
)
from app.services.lead_service import LeadService
from app.services.public_content_service import PublicContentService
from app.services.public_event_service import PublicEventService
from app.repositories.visitor_intent_repository import VisitorIntentRepository

client = TestClient(app)


def test_get_public_home(monkeypatch) -> None:
    def fake_get_home(self):
        return PublicHomeResponse(
            page="home",
            sections=[
                PublicContentSection(
                    section_key="hero",
                    title="Revision de historia laboral",
                    subtitle="Orientacion inicial antes del analisis completo.",
                    cta_label="Iniciar analisis",
                    cta_url="/registro",
                )
            ],
            legal_notice="Labora usa IA asistida.",
            updated_at="2026-05-11T00:00:00",
        )

    monkeypatch.setattr(PublicContentService, "get_home", fake_get_home)

    response = client.get("/api/v1/public/home")

    assert response.status_code == 200
    data = response.json()
    assert data["page"] == "home"
    assert data["sections"][0]["sectionKey"] == "hero"
    assert data["sections"][0]["ctaUrl"] == "/registro"
    assert data["legalNotice"] == "Labora usa IA asistida."


def test_get_public_faqs(monkeypatch) -> None:
    captured: dict = {}

    def fake_list_faqs(self, *, category=None, limit=20):
        captured["category"] = category
        captured["limit"] = limit
        return FaqListResponse(
            items=[
                FaqResponse(
                    id="faq-1",
                    question="Labora reemplaza a un abogado?",
                    answer="No.",
                    category="ia",
                    sort_order=1,
                )
            ]
        )

    monkeypatch.setattr(PublicContentService, "list_faqs", fake_list_faqs)

    response = client.get("/api/v1/public/faqs?category=ia&limit=5")

    assert response.status_code == 200
    assert captured == {"category": "ia", "limit": 5}
    assert response.json()["items"][0]["sortOrder"] == 1


def test_create_lead_success(monkeypatch) -> None:
    public_rate_limiter.clear()
    captured: dict = {}

    def fake_create(self, payload, *, ip_address, user_agent):
        captured["phone"] = payload.phone
        captured["email"] = str(payload.email)
        captured["ip_address"] = ip_address
        return LeadCreateResponse(
            id="lead-1",
            status="new",
            message="Recibimos tus datos.",
            next_action=NextAction(label="Crear cuenta", url="/registro"),
        )

    monkeypatch.setattr(LeadService, "create", fake_create)

    response = client.post(
        "/api/v1/leads",
        json={
            "fullName": "Maria Perez",
            "email": "maria@example.com",
            "phone": "+57 300 111 2233",
            "serviceInterest": "historia_laboral",
            "message": "Quiero saber si mi historia laboral tiene inconsistencias.",
            "source": "landing",
            "acceptedPrivacyNotice": True,
            "utm": {"source": "google", "medium": "cpc"},
        },
        headers={"x-forwarded-for": "203.0.113.10"},
    )

    assert response.status_code == 201
    assert response.json()["nextAction"]["url"] == "/registro"
    assert captured["phone"] == "+573001112233"
    assert captured["email"] == "maria@example.com"
    assert captured["ip_address"] == "203.0.113.10"


def test_create_lead_requires_privacy_notice() -> None:
    public_rate_limiter.clear()

    response = client.post(
        "/api/v1/leads",
        json={
            "fullName": "Maria Perez",
            "email": "maria@example.com",
            "acceptedPrivacyNotice": False,
        },
    )

    assert response.status_code == 422


def test_create_lead_rate_limited(monkeypatch) -> None:
    public_rate_limiter.clear()

    def fake_create(self, payload, *, ip_address, user_agent):
        return LeadCreateResponse(
            id="lead-1",
            status="new",
            message="Recibimos tus datos.",
            next_action=NextAction(label="Crear cuenta", url="/registro"),
        )

    monkeypatch.setattr(LeadService, "create", fake_create)

    payload = {
        "fullName": "Maria Perez",
        "email": "rate@example.com",
        "acceptedPrivacyNotice": True,
    }
    for _ in range(3):
        assert client.post("/api/v1/leads", json=payload).status_code == 201

    response = client.post("/api/v1/leads", json=payload)

    assert response.status_code == 429


def test_create_public_event(monkeypatch) -> None:
    public_rate_limiter.clear()

    def fake_create(self, payload, *, ip_address, user_agent):
        assert payload.event_name == "landing_publica.viewed"
        assert payload.metadata == {"page": "home"}
        return PublicEventResponse(id="event-1")

    monkeypatch.setattr(PublicEventService, "create", fake_create)

    response = client.post(
        "/api/v1/public/events",
        json={
            "eventName": "landing_publica.viewed",
            "anonymousId": "anon_123",
            "metadata": {"page": "home"},
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_public_event_rejects_sensitive_metadata() -> None:
    public_rate_limiter.clear()

    response = client.post(
        "/api/v1/public/events",
        json={
            "eventName": "landing_publica.viewed",
            "metadata": {"salary": "1000000"},
        },
    )

    assert response.status_code == 422


def test_intent_classification_low_confidence(monkeypatch) -> None:
    public_rate_limiter.clear()
    monkeypatch.setattr(
        VisitorIntentRepository,
        "create",
        lambda self, **kwargs: None,
    )

    response = client.post(
        "/api/v1/public/intent-classification",
        json={"text": "Hola", "anonymousId": "anon_123"},
    )

    assert response.status_code == 200
    assert response.json()["intent"] == "general_commercial_question"
    assert response.json()["requiresHumanFollowup"] is True
