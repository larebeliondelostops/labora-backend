from datetime import date, timedelta
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
from app.models.questionnaire import (
    AiQuestionnaireEvent,
    AnswerVersion,
    CaseProfile,
    CaseQuestionnaireSession,
    ConditionalRule,
    Question,
    QuestionnaireAnswer,
    QuestionnaireTemplate,
    QuestionOption,
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
    QuestionnaireTemplate.__table__,
    Question.__table__,
    ConditionalRule.__table__,
    CaseQuestionnaireSession.__table__,
    QuestionOption.__table__,
    QuestionnaireAnswer.__table__,
    CaseProfile.__table__,
    AiQuestionnaireEvent.__table__,
    AnswerVersion.__table__,
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
    Base.metadata.drop_all(engine, tables=list(reversed(TABLES)))


def test_get_questionnaire_creates_session_and_prefills_case_data(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)

    response = client.get(f"/api/v1/cases/{case_id}/questionnaire", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["template"]["code"] == "guided_case_v1"
    assert data["session"]["status"] == "in_progress"
    assert "birth_date" in data["visibleQuestions"]
    assert "public_entity_names" not in data["visibleQuestions"]

    base_questions = {
        question["code"]: question
        for section in data["sections"]
        for question in section["questions"]
    }
    assert base_questions["birth_date"]["answer"]["value"] == "1965-03-12"
    assert base_questions["pension_fund"]["answer"]["value"] == "Colpensiones"


def test_saving_answers_reveals_conditional_questions_and_profile_preview(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    session_id = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/start",
        json={"templateCode": "guided_case_v1"},
        headers=headers,
    ).json()["session"]["id"]

    response = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/answers",
        json={
            "sessionId": session_id,
            "answers": [
                {"questionCode": "case_goal", "value": "missing_weeks"},
                {"questionCode": "current_pension_status", "value": "not_pensioned_yet"},
                {"questionCode": "has_resolution", "value": False},
                {"questionCode": "has_prior_claim", "value": False},
                {"questionCode": "believes_missing_weeks", "value": True},
            ],
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "missing_weeks_description" in data["newlyVisibleQuestions"]
    assert data["profilePreview"]["hasMissingWeeksClaim"] is True
    assert data["profilePreview"]["requiresReview"] is True
    assert any(item["code"] == "missing_weeks_supports" for item in data["profilePreview"]["missingDocuments"])


def test_submit_reports_missing_required_answers(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    session_id = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/start",
        json={"templateCode": "guided_case_v1"},
        headers=headers,
    ).json()["session"]["id"]

    response = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/submit",
        json={"sessionId": session_id, "confirmAccuracy": True},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_REQUIRED_ANSWERS"
    missing_codes = {
        item["questionCode"]
        for item in response.json()["error"]["details"]["missingQuestions"]
    }
    assert "case_goal" in missing_codes
    assert "current_pension_status" in missing_codes


def test_submit_completed_profile_without_review(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    session_id = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/start",
        json={"templateCode": "guided_case_v1"},
        headers=headers,
    ).json()["session"]["id"]
    _save_normal_answers(client, headers, case_id, session_id)

    response = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/submit",
        json={"sessionId": session_id, "confirmAccuracy": True},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["profile"]["requiresReview"] is False
    assert data["profile"]["detectedRoute"] == "labor_history_analysis"

    profile = client.get(f"/api/v1/cases/{case_id}/profile", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["profile"]["confidence"] > 0


def test_patch_answer_creates_answer_version(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    session_id = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/start",
        json={"templateCode": "guided_case_v1"},
        headers=headers,
    ).json()["session"]["id"]
    _save_normal_answers(client, headers, case_id, session_id)

    questionnaire = client.get(f"/api/v1/cases/{case_id}/questionnaire", headers=headers).json()
    answer_id = _answer_id(questionnaire, "case_goal")
    patched = client.patch(
        f"/api/v1/answers/{answer_id}",
        json={"value": "recognition", "changeReason": "Correccion del usuario"},
        headers=headers,
    )

    assert patched.status_code == 200
    assert patched.json()["answer"]["version"] == 2
    db = session_factory()
    try:
        assert db.query(AnswerVersion).count() == 1
    finally:
        db.close()


def test_foreign_user_cannot_access_questionnaire(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    _other_id, other_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id)

    response = client.get(f"/api/v1/cases/{case_id}/questionnaire", headers=other_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CASE_ACCESS_DENIED"
    assert owner_headers != other_headers


def _save_normal_answers(client: TestClient, headers: dict[str, str], case_id: str, session_id: str) -> None:
    response = client.post(
        f"/api/v1/cases/{case_id}/questionnaire/answers",
        json={
            "sessionId": session_id,
            "answers": [
                {"questionCode": "case_goal", "value": "labor_history_review"},
                {"questionCode": "current_pension_status", "value": "not_pensioned_yet"},
                {"questionCode": "has_resolution", "value": False},
                {"questionCode": "has_prior_claim", "value": False},
                {"questionCode": "believes_missing_weeks", "value": False},
                {"questionCode": "has_public_sector_work", "value": False},
                {"questionCode": "has_teacher_history", "value": False},
                {"questionCode": "believes_wrong_allowance", "value": False},
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200


def _answer_id(questionnaire: dict, question_code: str) -> str:
    for section in questionnaire["sections"]:
        for question in section["questions"]:
            if question["code"] == question_code:
                return question["answer"]["id"]
    raise AssertionError(f"No answer for {question_code}")


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
            holder_birth_date=date(1965, 3, 12),
            holder_email="maria@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            pension_fund_or_entity="Colpensiones",
            situation_type="not_pensioned_yet",
            status="created",
            current_step="case_created",
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
                slug=f"{consent_type}-{uuid4().hex[:8]}",
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
