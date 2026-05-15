from datetime import timedelta
from types import SimpleNamespace
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
from app.models.document import (
    Document,
    DocumentHash,
    DocumentPage,
    DocumentType,
    DocumentValidation,
    FileUpload,
)
from app.models.document_precheck import (
    AiConfidence,
    DocumentIssue,
    DocumentPrecheck,
    OcrJob,
    OcrPageResult,
)
from app.models.user import User
from app.services.ai_provider import (
    AiProviderError,
    AiInvalidResponseError,
    MockAiProvider,
    OpenAiCompatibleProvider,
)
from app.services.consent_service import (
    REQUIRED_CONSENT_TYPES,
    calculate_document_hash,
)
from app.services.document_storage_service import DocumentStorageService, StorageProviderError
from app.services.document_precheck_service import _classification_input
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
    DocumentType.__table__,
    Document.__table__,
    DocumentPage.__table__,
    FileUpload.__table__,
    DocumentHash.__table__,
    DocumentValidation.__table__,
    DocumentPrecheck.__table__,
    OcrJob.__table__,
    OcrPageResult.__table__,
    DocumentIssue.__table__,
    AiConfidence.__table__,
]

TITLES = {
    "terms_and_conditions": "Terminos y condiciones",
    "personal_data_processing": "Tratamiento de datos personales",
    "sensitive_data_processing": "Tratamiento de datos sensibles",
    "electronic_means": "Medios electronicos",
    "ai_scope_acknowledgement": "Alcance IA",
}


@pytest.fixture()
def client_and_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    monkeypatch.setattr(settings, "AI_API_KEY", "")
    monkeypatch.setattr(settings, "AI_BASE_URL", "")
    monkeypatch.setattr(settings, "AI_MODEL", "")
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


def test_document_precheck_generates_completed_result_and_audit(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["documentId"] == document_id
    assert data["status"] == "completed"
    assert data["decision"] == "suitable"
    assert data["trafficLight"] == "green"
    assert data["confidenceScore"] >= 0.85
    assert data["ocr"]["textDetected"] is True
    assert data["ai"]["provider"] == "mock"

    listed = client.get(f"/api/v1/cases/{case_id}/document-precheck", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["precheckId"] == data["precheckId"]

    db = session_factory()
    try:
        event_types = {event.event_type for event in db.query(AuditEvent).all()}
        assert "ia_documental_preliminar.created" in event_types
        assert "ia_documental_preliminar.completed" in event_types
    finally:
        db.close()


def test_document_precheck_requires_consent(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_document_precheck_blocks_foreign_case_access(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    other_id, other_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, other_id)
    case_id = _create_case_row(session_factory, owner_id)
    document_id = _create_document_row(
        session_factory,
        user_id=owner_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    denied = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=other_headers,
    )

    assert owner_headers != other_headers
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "UNAUTHORIZED"


def test_document_precheck_corrupted_pdf_generates_critical_issue(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=b"not a pdf",
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "blocked"
    assert data["decision"] == "requires_reupload"
    assert data["trafficLight"] == "red"
    assert data["issues"][0]["code"] == "pdf_corrupted"
    assert data["issues"][0]["severity"] == "critical"


def test_document_precheck_valid_pdf_with_observations_returns_yellow(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_low_density_labor_pdf(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "completed"
    assert data["decision"] == "suitable_with_observations"
    assert data["trafficLight"] == "yellow"
    assert 0 < data["confidenceScore"] < 0.85
    assert data["ocr"]["textDetected"] is True


def test_document_precheck_textless_pdf_requires_reupload(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_textless_pdf(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "blocked"
    assert data["decision"] == "requires_reupload"
    assert data["trafficLight"] == "red"
    assert data["confidenceScore"] == 0
    assert data["ocr"]["textDetected"] is False
    assert {issue["code"] for issue in data["issues"]} >= {"no_text_detected", "ocr_provider_not_configured"}


def test_document_precheck_wrong_document_type_is_unsupported(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_wrong_document_pdf(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "blocked"
    assert data["decision"] == "unsupported"
    assert data["trafficLight"] == "red"
    assert data["ocr"]["textDetected"] is True
    assert {issue["code"] for issue in data["issues"]} >= {"not_labor_or_pension_document"}


def test_document_precheck_storage_read_error_is_technical_failure(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    def fail_read(self, storage_key):
        raise StorageProviderError("MinIO no disponible.")

    monkeypatch.setattr(DocumentStorageService, "read", fail_read)

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "error"
    assert data["decision"] == "failed"
    assert data["trafficLight"] == "red"
    assert data["issues"][0]["code"] == "storage_read_error"
    assert data["confidenceScore"] == 0


def test_ocr_preview_endpoint_returns_page_preview(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    created = client.post(
        f"/api/v1/documents/{document_id}/ocr-preview",
        json={"maxPages": 2, "includeTextPreview": True},
        headers=headers,
    )
    assert created.status_code == 202
    assert created.json()["status"] == "completed"

    fetched = client.get(f"/api/v1/documents/{document_id}/ocr-preview", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["pages"][0]["textPreview"]


def test_ocr_preview_get_generates_preview_if_missing(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    fetched = client.get(f"/api/v1/documents/{document_id}/ocr-preview", headers=headers)

    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["status"] == "completed"
    assert payload["pagesTotal"] == 1
    assert payload["pagesProcessed"] == 1
    assert payload["textDetected"] is True
    assert payload["pages"][0]["textPreview"]


def test_latest_precheck_response_includes_ocr_pages_for_document_filter(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )
    created = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )
    assert created.status_code == 202

    response = client.get(
        f"/api/v1/cases/{case_id}/document-precheck",
        params={"documentId": document_id, "latest": True},
        headers=headers,
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["ocr"]["status"] == "completed"
    assert item["ocr"]["pages"][0]["textPreview"]


def test_invalid_ai_response_marks_precheck_as_error(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    class InvalidProvider:
        def classify_document(self, input_payload):
            raise AiInvalidResponseError(
                "AI_PROVIDER_INVALID_RESPONSE",
                "JSON invalido.",
            )

    monkeypatch.setattr(
        "app.services.document_precheck_service.ai_provider_factory",
        lambda: InvalidProvider(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "error"
    assert data["decision"] == "failed"
    assert data["trafficLight"] == "red"
    assert data["issues"][0]["code"] == "ai_provider_error"
    assert data["issues"][0]["metadata"]["provider_error_code"] == "AI_PROVIDER_INVALID_RESPONSE"


def test_ai_provider_error_marks_precheck_as_error(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    class FailingProvider:
        provider_name = "fake"
        model = "fake-model"

        def classify_document(self, input_payload):
            raise AiProviderError(
                "AI_PROVIDER_ERROR",
                "Proveedor temporalmente indisponible.",
                details={"statusCode": 502, "providerMessage": "bad gateway"},
            )

    monkeypatch.setattr(
        "app.services.document_precheck_service.ai_provider_factory",
        lambda: FailingProvider(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "error"
    assert data["decision"] == "failed"
    assert data["trafficLight"] == "red"
    assert data["ocr"]["textDetected"] is True
    assert data["issues"][0]["code"] == "ai_provider_error"
    assert data["issues"][0]["message"] == "No fue posible completar la clasificacion IA por un error tecnico."
    assert data["issues"][0]["suggestedAction"] == "wait_and_retry"
    assert data["issues"][0]["metadata"]["provider_error_code"] == "AI_PROVIDER_ERROR"
    assert data["issues"][0]["metadata"]["provider_status_code"] == 502
    assert data["issues"][0]["metadata"]["retryable"] is True
    assert data["summary"] == "No pudimos completar la clasificacion automatica por un error tecnico."


def test_force_true_retries_failed_precheck(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_labor_history_pdf(),
    )

    class FlakyProvider:
        provider_name = "fake"
        model = "fake-model"

        def __init__(self) -> None:
            self.calls = 0

        def classify_document(self, input_payload):
            self.calls += 1
            if self.calls == 1:
                raise AiProviderError("AI_PROVIDER_ERROR", "Fallo temporal.")
            return MockAiProvider().classify_document(input_payload)

    provider = FlakyProvider()
    monkeypatch.setattr(
        "app.services.document_precheck_service.ai_provider_factory",
        lambda: provider,
    )

    failed = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )
    retried = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id, "force": True},
        headers=headers,
    )

    assert failed.status_code == 202
    assert failed.json()["status"] == "error"
    assert retried.status_code == 202
    assert retried.json()["status"] == "completed"
    assert retried.json()["decision"] == "suitable"
    assert retried.json()["precheckId"] != failed.json()["precheckId"]

    latest = client.get(
        f"/api/v1/cases/{case_id}/document-precheck",
        params={"documentId": document_id, "latest": True},
        headers=headers,
    )
    assert latest.status_code == 200
    assert latest.json()["items"][0]["precheckId"] == retried.json()["precheckId"]


def test_classification_payload_trims_large_ocr_text() -> None:
    document = SimpleNamespace(
        id=uuid4(),
        case_id=uuid4(),
        original_filename="historia-laboral.pdf",
        mime_type="application/pdf",
        size_bytes=64_000,
    )
    long_text = "historia laboral semanas cotizadas colpensiones " * 500
    pages = [
        SimpleNamespace(
            page_number=index + 1,
            text_preview=long_text,
            confidence_score=0.91,
            text_density=0.8,
            is_blurry=False,
            is_rotated=False,
            has_table_like_content=True,
            detected_labels=["historia_laboral"],
        )
        for index in range(10)
    ]
    ocr_job = SimpleNamespace(
        pages=pages,
        pages_total=10,
        text_detected=True,
        avg_text_density=0.8,
    )

    payload = _classification_input(
        document=document,
        ocr_job=ocr_job,
        precheck_id=uuid4(),
        request_id="req-test",
    )
    pages = payload["ocrSignals"]["pages"]

    assert payload["fileMetadata"]["originalFilename"] == "historia-laboral.pdf"
    assert sum(len(page["textPreview"]) for page in pages) <= 12000
    assert all(len(page["textPreview"]) <= 2000 for page in pages)
    assert any(page["textPreviewTruncated"] for page in pages)
    assert "historia laboral" in " ".join(page["textPreview"].lower() for page in pages)


def test_ambiguous_document_requires_human_review_with_yellow_light(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case_row(session_factory, user_id)
    document_id = _create_document_row(
        session_factory,
        user_id=user_id,
        case_id=UUID(case_id),
        content=_ambiguous_labor_pdf(),
    )

    response = client.post(
        f"/api/v1/cases/{case_id}/document-precheck",
        json={"documentId": document_id},
        headers=headers,
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "requires_review"
    assert data["decision"] == "requires_human_review"
    assert data["trafficLight"] == "yellow"
    assert data["confidenceScore"] > 0
    assert data["issues"][0]["code"] == "human_review_required"


def test_mock_ai_provider_is_deterministic() -> None:
    payload = {
        "caseId": str(uuid4()),
        "documentId": str(uuid4()),
        "fileMetadata": {"mimeType": "application/pdf", "pagesTotal": 1, "sizeBytes": 10},
        "ocrSignals": {
            "textDetected": True,
            "avgTextDensity": 0.8,
            "pages": [
                {
                    "pageNumber": 1,
                    "textPreview": "historia laboral semanas cotizadas colpensiones",
                    "confidenceScore": 0.91,
                    "isBlurry": False,
                    "isRotated": False,
                    "hasTableLikeContent": True,
                }
            ],
        },
        "allowedDocumentTypes": ["historia_laboral"],
    }

    first = MockAiProvider().classify_document(payload)
    second = MockAiProvider().classify_document(payload)

    assert first.output.model_dump(by_alias=True) == second.output.model_dump(by_alias=True)
    assert first.output.traffic_light == "green"


def test_openai_compatible_provider_does_not_put_key_in_payload(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"documentType":"historia_laboral",'
                                '"isLaborOrPensionRelated":true,'
                                '"isSuitableForPreanalysis":true,'
                                '"trafficLight":"green",'
                                '"confidenceScore":0.91,'
                                '"summary":"Documento apto.",'
                                '"detectedSignals":["semanas"],'
                                '"issues":[],'
                                '"recommendedNextAction":"continue"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

    def fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_provider.requests.post", fake_post)
    provider = OpenAiCompatibleProvider(
        provider_name="deepseek",
        base_url="https://api.example.test",
        model="model-a",
        api_key="secret-key",
    )

    result = provider.classify_document(
        {
            "caseId": str(uuid4()),
            "documentId": str(uuid4()),
            "fileMetadata": {"mimeType": "application/pdf", "pagesTotal": 1, "sizeBytes": 10},
            "ocrSignals": {"textDetected": True, "avgTextDensity": 0.8, "pages": []},
            "allowedDocumentTypes": ["historia_laboral"],
        }
    )

    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in str(captured["json"])
    assert result.output.confidence_score == 0.91


def test_openai_compatible_provider_exposes_rejected_request_details(monkeypatch) -> None:
    class FakeResponse:
        status_code = 404
        text = '{"error":{"message":"model not found"}}'
        headers = {"x-request-id": "req-123", "cf-ray": "trace-456"}

        def json(self):
            return {"error": {"message": "model not found"}}

    def fake_post(url, *, json, headers, timeout):
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_provider.requests.post", fake_post)
    provider = OpenAiCompatibleProvider(
        provider_name="deepseek",
        base_url="https://api.example.test",
        model="model-a",
        api_key="secret-key",
    )

    with pytest.raises(AiProviderError) as exc_info:
        provider.classify_document(
            {
                "caseId": str(uuid4()),
                "documentId": str(uuid4()),
                "fileMetadata": {"mimeType": "application/pdf", "pagesTotal": 1, "sizeBytes": 10},
                "ocrSignals": {"textDetected": True, "avgTextDensity": 0.8, "pages": []},
                "allowedDocumentTypes": ["historia_laboral"],
            }
        )

    assert exc_info.value.code == "AI_PROVIDER_MODEL_NOT_FOUND"
    assert exc_info.value.details["statusCode"] == 404
    assert exc_info.value.details["providerMessage"] == "model not found"
    assert exc_info.value.details["providerErrorCategory"] == "model_not_found"
    assert exc_info.value.details["providerRequestId"] == "req-123"
    assert exc_info.value.details["providerTraceId"] == "trace-456"


def test_openai_compatible_provider_retries_without_response_format(monkeypatch) -> None:
    calls = []

    class RejectedResponseFormat:
        status_code = 400
        text = '{"error":{"message":"model does not support response_format"}}'

        def json(self):
            return {"error": {"message": "model does not support response_format"}}

    class AcceptedResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"documentType":"historia_laboral",'
                                '"isLaborOrPensionRelated":true,'
                                '"isSuitableForPreanalysis":true,'
                                '"trafficLight":"green",'
                                '"confidenceScore":0.91,'
                                '"summary":"Documento apto.",'
                                '"detectedSignals":["semanas"],'
                                '"issues":[],'
                                '"recommendedNextAction":"continue"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

    def fake_post(url, *, json, headers, timeout):
        calls.append(json)
        return RejectedResponseFormat() if len(calls) == 1 else AcceptedResponse()

    monkeypatch.setattr(settings, "AI_JSON_MODE", True)
    monkeypatch.setattr("app.services.ai_provider.requests.post", fake_post)
    provider = OpenAiCompatibleProvider(
        provider_name="deepseek",
        base_url="https://api.example.test",
        model="deepseek-chat",
        api_key="secret-key",
    )

    result = provider.classify_document(
        {
            "requestId": str(uuid4()),
            "precheckId": str(uuid4()),
            "caseId": str(uuid4()),
            "documentId": str(uuid4()),
            "fileMetadata": {"mimeType": "application/pdf", "pagesTotal": 1, "sizeBytes": 10},
            "ocrSignals": {"textDetected": True, "avgTextDensity": 0.8, "pages": []},
            "allowedDocumentTypes": ["historia_laboral"],
        }
    )

    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert result.output.confidence_score == 0.91


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
            holder_first_name="Ana",
            holder_last_name="Gomez",
            holder_document_type="CC",
            holder_document_number="10101010",
            holder_email="ana@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            situation_type="pensioned_with_doubts",
            status="documents_uploaded",
            current_step="documents_uploaded",
            next_best_action="start_preanalysis",
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
                permissions={"edit_case": True},
            )
        )
        db.commit()
        return str(case.id)
    finally:
        db.close()


def _create_document_row(
    session_factory,
    *,
    user_id: UUID,
    case_id: UUID,
    content: bytes,
) -> str:
    db = session_factory()
    now = utc_now()
    document_id = uuid4()
    storage = DocumentStorageService()
    storage_key = storage.build_storage_key(
        case_id=str(case_id),
        document_id=str(document_id),
        filename="historia-laboral.pdf",
    )
    storage.save(storage_key=storage_key, content=content, content_type="application/pdf")
    try:
        document = Document(
            id=document_id,
            case_id=case_id,
            uploaded_by_user_id=user_id,
            document_type_id=None,
            original_filename="historia-laboral.pdf",
            display_name=None,
            mime_type="application/pdf",
            extension="pdf",
            size_bytes=len(content),
            storage_bucket=storage.bucket_name,
            storage_key=storage_key,
            status="uploaded",
            validation_status="completed",
            classification_source="manual",
            is_primary=True,
            is_duplicate=False,
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.commit()
        return str(document.id)
    finally:
        db.close()


def _labor_history_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"historia laboral semanas cotizadas colpensiones periodos laborales empleador salario\n%%EOF"
    )


def _low_density_labor_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"historia laboral semanas cotizadas "
        + (b". " * 120)
        + b"\n%%EOF"
    )


def _textless_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
        b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
        b"3 0 obj <</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>> endobj\n"
        b"xref\ntrailer\n%%EOF"
    )


def _wrong_document_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"contrato de arrendamiento inmueble canon mensual deposito inventario llaves\n%%EOF"
    )


def _ambiguous_labor_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"documento pensional del trabajador con informacion incompleta para validar automaticamente\n%%EOF"
    )
