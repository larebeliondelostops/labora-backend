from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import app.models  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.case import CaseOwner, LaboraCase
from app.models.consent import LegalDocument, UserConsent
from app.models.document import Document, DocumentType
from app.models.payment import Order
from app.models.pension import CaseEntitlement, PensionSimulation, PensionSimulationScenario
from app.models.user import User
from app.services.consent_service import REQUIRED_CONSENT_TYPES, calculate_document_hash
from app.utils.dates import utc_now


TITLES = {
    "terms_and_conditions": "Terminos y condiciones",
    "personal_data_processing": "Tratamiento de datos personales",
    "sensitive_data_processing": "Tratamiento de datos sensibles",
    "electronic_means": "Medios electronicos",
    "ai_scope_acknowledgement": "Alcance IA",
}


def test_user_runs_free_rais_simulation_before_payment_and_sees_legal_paywall() -> None:
    client, session_factory, engine = _client_and_session()
    try:
        user_id, headers = _create_user(session_factory)
        _grant_required_consents(session_factory, user_id)
        case_id = _create_case_row(session_factory, user_id)
        _create_pension_history_document(session_factory, user_id=user_id, case_id=UUID(case_id))

        profile = client.patch(
            f"/api/v1/cases/{case_id}/pension/profile",
            headers=headers,
            json={
                "age": 41,
                "sex": "M",
                "fundName": "Proteccion",
                "inferredRegime": "RAIS",
                "currentWeeks": 803.57,
                "currentIndividualAccountBalance": 185133502,
                "currentMonthlyIncomeReference": 6000000,
                "dataConfidenceScore": 0.88,
            },
        )
        assert profile.status_code == 200
        assert profile.json()["missingFields"] == []

        assumptions = client.post(
            f"/api/v1/cases/{case_id}/pension/assumptions",
            headers=headers,
            json={
                "targetAge": 62,
                "incomeReferenceMethod": "user_input",
                "userIncomeInput": 6000000,
                "realReturnAnnualConservative": 0.01,
                "realReturnAnnualBase": 0.03,
                "realReturnAnnualOptimistic": 0.05,
                "realIncomeGrowthAnnual": 0.01,
                "individualAccountRate": 0.115,
                "annuityFactorMethod": "simple_years",
                "expectedPaymentYears": 20,
            },
        )
        assert assumptions.status_code == 201

        simulated = client.post(f"/api/v1/cases/{case_id}/pension/simulations/run", headers=headers)
        assert simulated.status_code == 200
        body = simulated.json()
        assert body["visibleBeforePayment"] is True
        assert "No reemplaza la liquidacion oficial" in body["disclaimer"]
        assert {item["scenario"] for item in body["scenarios"]} == {"conservative", "base", "optimistic"}
        assert body["recommendedNextStep"]["requiresPayment"] is True

        paywall = client.get(f"/api/v1/cases/{case_id}/paywall/legal-draft", headers=headers)
        assert paywall.status_code == 200
        assert paywall.json()["freeSimulationCompleted"] is True
        assert paywall.json()["paymentRequired"] is True

        order_response = client.post(
            f"/api/v1/cases/{case_id}/payments/legal-draft",
            headers=headers,
            json={"productCode": "LEGAL_DRAFT_GENERATION"},
        )
        assert order_response.status_code == 201
        order = order_response.json()["order"]
        assert order["productCode"] == "LEGAL_DRAFT_GENERATION"
        assert order["totalAmount"] == 150000

        db = session_factory()
        try:
            assert db.query(PensionSimulation).count() == 1
            assert db.query(PensionSimulationScenario).count() == 3
            assert (
                db.query(CaseEntitlement)
                .filter(
                    CaseEntitlement.case_id == UUID(case_id),
                    CaseEntitlement.entitlement == "free_pension_simulation",
                    CaseEntitlement.active.is_(True),
                )
                .count()
                == 1
            )
            stored_order = db.get(Order, UUID(order["id"]))
            assert stored_order.product_code == "LEGAL_DRAFT_GENERATION"
        finally:
            db.close()
    finally:
        _dispose_client(client, engine)


def test_simulation_requires_pension_history_document() -> None:
    client, session_factory, engine = _client_and_session()
    try:
        user_id, headers = _create_user(session_factory)
        _grant_required_consents(session_factory, user_id)
        case_id = _create_case_row(session_factory, user_id)

        profile = client.patch(
            f"/api/v1/cases/{case_id}/pension/profile",
            headers=headers,
            json={
                "age": 41,
                "sex": "M",
                "fundName": "Proteccion",
                "inferredRegime": "RAIS",
                "currentWeeks": 803.57,
                "currentIndividualAccountBalance": 185133502,
                "currentMonthlyIncomeReference": 3200000,
            },
        )
        assert profile.status_code == 200

        simulated = client.post(f"/api/v1/cases/{case_id}/pension/simulations/run", headers=headers)
        assert simulated.status_code == 409
        assert simulated.json()["error"]["code"] == "PENSION_HISTORY_DOCUMENT_REQUIRED"
    finally:
        _dispose_client(client, engine)


def _client_and_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, TestingSessionLocal, engine


def _dispose_client(client: TestClient, engine) -> None:
    client.close()
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


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
            document = LegalDocument(
                type=consent_type,
                title=title,
                slug=f"{consent_type}-{uuid4().hex}",
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
            holder_birth_date=date(1985, 3, 12),
            holder_email="maria@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            pension_fund_or_entity="Proteccion",
            pension_regime="RAIS",
            case_goal="estimate_future_pension",
            current_situation="not_pensioned",
            situation_type="not_pensioned_yet",
            status="ready_for_documents",
            current_step="documents_pending",
            next_best_action="upload_documents",
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
        db.add(CaseEntitlement(case_id=case.id, entitlement="free_pension_simulation", active=True))
        db.commit()
        return str(case.id)
    finally:
        db.close()


def _create_pension_history_document(session_factory, *, user_id: UUID, case_id: UUID) -> None:
    db = session_factory()
    now = utc_now()
    try:
        document_type = DocumentType(
            code="pension_history_pdf",
            name="Historia laboral AFP",
            description="Historia laboral del fondo de pensiones.",
            category="pension",
            is_required_for_basic_flow=True,
            is_primary_candidate=True,
            allowed_mime_types=["application/pdf"],
            max_size_mb=25,
            sort_order=1,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(document_type)
        db.flush()
        db.add(
            Document(
                case_id=case_id,
                uploaded_by_user_id=user_id,
                document_type_id=document_type.id,
                original_filename="historia_laboral.pdf",
                display_name="Historia laboral",
                mime_type="application/pdf",
                extension=".pdf",
                size_bytes=1024,
                storage_bucket="local",
                storage_key=f"cases/{case_id}/historia_laboral.pdf",
                sha256_hash="b" * 64,
                status="validated",
                validation_status="completed",
                classification_source="user",
                is_primary=True,
                page_count=3,
                is_password_protected=False,
                is_corrupted=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()
