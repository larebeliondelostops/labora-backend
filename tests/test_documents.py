from datetime import timedelta
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
from app.models.user import User
from app.services.consent_service import (
    REQUIRED_CONSENT_TYPES,
    calculate_document_hash,
)
from app.services.document_storage_service import (
    DocumentStorageService,
    _normalize_minio_endpoint,
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
    DocumentType.__table__,
    Document.__table__,
    DocumentPage.__table__,
    FileUpload.__table__,
    DocumentHash.__table__,
    DocumentValidation.__table__,
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
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "BACKEND_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "API_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "PUBLIC_API_URL", "")
    monkeypatch.setattr(settings, "MINIO_BUCKET", "documents")
    monkeypatch.setattr(settings, "MINIO_PRESIGNED_UPLOAD_TTL_SECONDS", 900)

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


def test_document_types_and_multipart_upload_flow(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)

    types_response = client.get("/api/v1/document-types", headers=headers)
    assert types_response.status_code == 200
    assert "historia_laboral" in {item["code"] for item in types_response.json()["items"]}

    created = _upload_pdf(
        client,
        headers,
        case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )

    assert created.status_code == 201
    created_data = created.json()
    document_id = created_data["document"]["id"]
    assert created_data["document"]["status"] == "validated"
    assert created_data["document"]["validationStatus"] == "completed"
    assert created_data["document"]["isPrimary"] is True
    assert created_data["upload"]["method"] == "multipart"

    listed = client.get(f"/api/v1/cases/{case_id}/documents", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1
    item = listed.json()["items"][0]
    assert item["documentType"]["code"] == "historia_laboral"
    assert item["pageCount"] == 1
    assert item["isDuplicate"] is False

    detail = client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["validation"]["result"] == "accepted"
    assert detail.json()["validation"]["checks"]["looksLikeLaborHistory"] is True

    view_url = client.get(f"/api/v1/documents/{document_id}/view-url", headers=headers)
    assert view_url.status_code == 200
    assert view_url.json()["expiresInSeconds"] == 300
    assert view_url.json()["url"].startswith(
        f"http://localhost:8000/api/v1/documents/{document_id}/file"
    )

    readiness = client.get(f"/api/v1/cases/{case_id}/document-readiness", headers=headers)
    assert readiness.status_code == 200
    assert readiness.json()["readinessStatus"] == "ready_for_preanalysis"
    assert readiness.json()["hasPrimaryLaborHistory"] is True
    assert readiness.json()["nextAction"] == "continue_to_preanalysis"

    case_detail = client.get(f"/api/v1/cases/{case_id}", headers=headers)
    assert case_detail.status_code == 200
    assert case_detail.json()["status"] == "documents_uploaded"
    assert case_detail.json()["currentStep"] == "documents_uploaded"
    assert case_detail.json()["nextBestAction"] == "start_preanalysis"
    assert "start_preanalysis" in case_detail.json()["allowedActions"]

    db = session_factory()
    try:
        document = db.get(Document, UUID(document_id))
        assert document.sha256_hash is not None
        hash_types = {
            item.hash_type
            for item in db.query(DocumentHash).filter(
                DocumentHash.document_id == document.id,
            )
        }
        assert hash_types == {"sha256", "duplicate_scope"}
        event_types = {event.event_type for event in db.query(AuditEvent).all()}
        assert "carga_documental.created" in event_types
        assert "carga_documental.validation_completed" in event_types
        assert "carga_documental.viewed" in event_types
    finally:
        db.close()


def test_upload_blocks_missing_consent_and_foreign_case_access(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _grant_required_consents(session_factory, owner_id)
    case_id = _create_case(client, owner_headers)
    _other_id, other_headers = _create_user(session_factory)

    denied = _upload_pdf(
        client,
        other_headers,
        case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CASE_ACCESS_DENIED"

    no_consent_id, no_consent_headers = _create_user(session_factory)
    no_consent_case_id = _create_case_row(session_factory, no_consent_id)
    blocked = _upload_pdf(
        client,
        no_consent_headers,
        no_consent_case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_document_view_url_uses_public_api_base_url_in_production(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "BACKEND_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "API_PUBLIC_BASE_URL", "https://labora-api.centralspike.com")
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)
    created = _upload_pdf(
        client,
        headers,
        case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )
    document_id = created.json()["document"]["id"]

    response = client.get(f"/api/v1/documents/{document_id}/view-url", headers=headers)

    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith(f"https://labora-api.centralspike.com/api/v1/documents/{document_id}/file")
    assert "localhost" not in url
    assert "127.0.0.1" not in url


def test_document_view_url_rejects_localhost_in_production(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "BACKEND_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "API_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings, "PUBLIC_API_URL", "")
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)
    created = _upload_pdf(
        client,
        headers,
        case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )
    document_id = created.json()["document"]["id"]

    response = client.get(f"/api/v1/documents/{document_id}/view-url", headers=headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PUBLIC_URL_CONFIGURATION_ERROR"


def test_document_validations_duplicates_update_replace_and_delete(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)

    first = _upload_pdf(
        client,
        headers,
        case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    ).json()["document"]
    duplicate = _upload_pdf(
        client,
        headers,
        case_id,
        filename="historia-laboral-copia.pdf",
        document_type_code="historia_laboral",
        is_primary=False,
        content=_labor_history_pdf(),
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["document"]["status"] == "requires_review"

    db = session_factory()
    try:
        duplicate_document = db.get(Document, UUID(duplicate.json()["document"]["id"]))
        assert duplicate_document.is_duplicate is True
        assert duplicate_document.duplicate_of_document_id == UUID(first["id"])
    finally:
        db.close()

    updated = client.patch(
        f"/api/v1/documents/{duplicate.json()['document']['id']}",
        json={
            "displayName": "Historia laboral duplicada",
            "documentTypeCode": "historia_laboral",
            "isPrimary": False,
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["classificationSource"] == "manual"

    support = _upload_pdf(
        client,
        headers,
        case_id,
        filename="certificacion-laboral.pdf",
        document_type_code="certificacion_laboral",
        is_primary=False,
        content=_support_pdf(),
    ).json()["document"]
    deleted = client.delete(f"/api/v1/documents/{support['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    replacement = client.post(
        f"/api/v1/documents/{first['id']}/replace",
        json={
            "originalFilename": "historia-laboral-corregida.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": len(_labor_history_pdf(extra=b" corregida")),
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
        headers=headers,
    )
    assert replacement.status_code == 200
    assert replacement.json()["oldStatus"] == "replaced"
    assert replacement.json()["newStatus"] == "uploading"


def test_same_file_for_different_holder_is_not_duplicate(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    first_case_id = _create_case_row(
        session_factory,
        user_id,
        holder_first_name="Ana",
        holder_last_name="Gomez",
        holder_document_number="10101010",
    )
    second_case_id = _create_case_row(
        session_factory,
        user_id,
        holder_first_name="Luis",
        holder_last_name="Perez",
        holder_document_number="20202020",
    )

    first = _upload_pdf(
        client,
        headers,
        first_case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )
    second = _upload_pdf(
        client,
        headers,
        second_case_id,
        filename="historia-laboral.pdf",
        document_type_code="historia_laboral",
        is_primary=True,
        content=_labor_history_pdf(),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["document"]["status"] == "validated"
    assert second.json()["document"]["status"] == "validated"

    db = session_factory()
    try:
        second_document = db.get(Document, UUID(second.json()["document"]["id"]))
        assert second_document.is_duplicate is False
        assert second_document.duplicate_of_document_id is None
    finally:
        db.close()


def test_upload_rejects_bad_type_size_and_corrupted_content(client_and_session) -> None:
    client, session_factory = client_and_session
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)

    bad_type = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        data={"documentTypeCode": "historia_laboral", "isPrimary": "true"},
        files={"file": ("malware.exe", b"not a pdf", "application/octet-stream")},
    )
    assert bad_type.status_code == 422
    assert bad_type.json()["error"]["code"] == "DOCUMENT_MIME_TYPE_NOT_ALLOWED"

    too_large = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        json={
            "originalFilename": "historia-laboral.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": 51 * 1024 * 1024,
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "DOCUMENT_SIZE_EXCEEDED"

    corrupted = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        data={"documentTypeCode": "historia_laboral", "isPrimary": "true"},
        files={"file": ("historia-laboral.pdf", b"not a pdf", "application/pdf")},
    )
    assert corrupted.status_code == 422
    assert corrupted.json()["error"]["code"] == "DOCUMENT_CORRUPTED"


def test_signed_url_upload_payload_uses_minio_put_url(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    _use_fake_minio(monkeypatch, object_exists=False)
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)

    created = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        json={
            "originalFilename": "historia-laboral.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": len(_labor_history_pdf()),
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
    )

    assert created.status_code == 201
    upload = created.json()["upload"]
    assert upload["method"] == "signed_url"
    assert upload["status"] == "initiated"
    assert upload["uploadUrl"].startswith("http://localhost:9000/documents/")
    assert "/file" not in upload["uploadUrl"]
    assert upload["headers"] == {"Content-Type": "application/pdf"}


def test_complete_upload_fails_when_minio_object_is_missing(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    _use_fake_minio(monkeypatch, object_exists=False)
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)
    created = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        json={
            "originalFilename": "historia-laboral.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": len(_labor_history_pdf()),
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
    )
    assert created.status_code == 201
    document_id = created.json()["document"]["id"]

    completed = client.post(
        f"/api/v1/documents/{document_id}/complete-upload",
        headers=headers,
    )

    assert completed.status_code == 409
    assert completed.json()["error"]["code"] == "STORAGE_PROVIDER_ERROR"
    db = session_factory()
    try:
        upload = db.query(FileUpload).filter(FileUpload.document_id == UUID(document_id)).one()
        assert upload.status == "failed"
        assert upload.error_code == "STORAGE_PROVIDER_ERROR"
    finally:
        db.close()


def test_complete_upload_reads_private_minio_object(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    _use_fake_minio(monkeypatch, object_exists=True, content=_labor_history_pdf())
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)
    created = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        json={
            "originalFilename": "historia-laboral.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": len(_labor_history_pdf()),
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
    )
    assert created.status_code == 201
    document_id = created.json()["document"]["id"]

    completed = client.post(
        f"/api/v1/documents/{document_id}/complete-upload",
        headers=headers,
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "validated"
    assert completed.json()["validationStatus"] == "completed"
    assert {job["type"] for job in completed.json()["jobs"]} == {
        "document_validation",
        "document_classification",
    }


def test_view_url_uses_minio_public_presigned_get_url(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    _use_fake_minio(
        monkeypatch,
        object_exists=True,
        content=_labor_history_pdf(),
        public_endpoint="https://minio.centralspike.com",
    )
    monkeypatch.setattr(settings, "APP_ENV", "production")
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)
    created = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        json={
            "originalFilename": "historia-laboral.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": len(_labor_history_pdf()),
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
    )
    assert created.status_code == 201
    document_id = created.json()["document"]["id"]

    response = client.get(f"/api/v1/documents/{document_id}/view-url", headers=headers)

    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith("https://minio.centralspike.com/documents/")
    assert "X-Amz-Signature=fake-get" in url
    assert f"/api/v1/documents/{document_id}/file" not in url
    assert "localhost" not in url
    assert "labora-minio" not in url


def test_minio_signed_urls_reject_internal_public_endpoint_in_production(client_and_session, monkeypatch) -> None:
    client, session_factory = client_and_session
    _use_fake_minio(
        monkeypatch,
        object_exists=True,
        content=_labor_history_pdf(),
        public_endpoint="labora-minio:9000",
    )
    monkeypatch.setattr(settings, "APP_ENV", "production")
    user_id, headers = _create_user(session_factory)
    _grant_required_consents(session_factory, user_id)
    case_id = _create_case(client, headers)
    created = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        json={
            "originalFilename": "historia-laboral.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": len(_labor_history_pdf()),
            "documentTypeCode": "historia_laboral",
            "isPrimary": True,
        },
    )
    assert created.status_code == 502
    assert created.json()["error"]["code"] == "STORAGE_PROVIDER_ERROR"


def test_minio_endpoint_scheme_controls_public_presigned_scheme() -> None:
    endpoint, secure = _normalize_minio_endpoint(
        "https://minio.centralspike.com",
        default_secure=False,
    )
    assert endpoint == "minio.centralspike.com"
    assert secure is True

    endpoint, secure = _normalize_minio_endpoint(
        "http://labora-minio:9000",
        default_secure=True,
    )
    assert endpoint == "labora-minio:9000"
    assert secure is False


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
            document = (
                db.query(LegalDocument)
                .filter(
                    LegalDocument.type == consent_type,
                    LegalDocument.version == "2026.05.01",
                )
                .one_or_none()
            )
            if document is None:
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


def _create_case(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post("/api/v1/cases", json=_case_payload(), headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def _create_case_row(
    session_factory,
    user_id: UUID,
    *,
    holder_first_name: str = "Ana",
    holder_last_name: str = "Gomez",
    holder_document_number: str = "10101010",
) -> str:
    db = session_factory()
    now = utc_now()
    try:
        case = LaboraCase(
            case_number=f"CASO-2026-{uuid4().hex[:6]}",
            owner_user_id=user_id,
            holder_type="self",
            holder_first_name=holder_first_name,
            holder_last_name=holder_last_name,
            holder_document_type="CC",
            holder_document_number=holder_document_number,
            holder_email="ana@example.com",
            holder_phone="+573001112233",
            acting_as_third_party=False,
            third_party_authorization_status="not_required",
            case_type_requested="labor_history_analysis",
            situation_type="pensioned_with_doubts",
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
                permissions={"edit_case": True},
            )
        )
        db.commit()
        return str(case.id)
    finally:
        db.close()


def _upload_pdf(
    client: TestClient,
    headers: dict[str, str],
    case_id: str,
    *,
    filename: str,
    document_type_code: str,
    is_primary: bool,
    content: bytes,
):
    return client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=headers,
        data={
            "documentTypeCode": document_type_code,
            "isPrimary": "true" if is_primary else "false",
        },
        files={"file": (filename, content, "application/pdf")},
    )


def _labor_history_pdf(extra: bytes = b"") -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"historia laboral semanas cotizadas colpensiones periodos laborales"
        + extra
        + b"\n%%EOF"
    )


def _support_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Catalog>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"certificacion laboral empleador salario cargo\n%%EOF"
    )


def _use_fake_minio(
    monkeypatch,
    *,
    object_exists: bool,
    content: bytes = b"",
    public_endpoint: str = "localhost:9000",
) -> None:
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "minio")
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "labora-minio:9000")
    monkeypatch.setattr(settings, "MINIO_PUBLIC_ENDPOINT", public_endpoint)
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "labora_minio")
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "labora_minio_password")
    monkeypatch.setattr(settings, "MINIO_BUCKET", "documents")
    monkeypatch.setattr(settings, "MINIO_REGION", "us-east-1")
    monkeypatch.setattr(settings, "MINIO_SECURE", False)
    monkeypatch.setattr(settings, "MINIO_PRESIGNED_UPLOAD_TTL_SECONDS", 900)

    class FakeMissingObjectError(Exception):
        code = "NoSuchKey"

    class FakeMinioResponse:
        def __init__(self, response_content: bytes) -> None:
            self.response_content = response_content

        def read(self):
            return self.response_content

        def stream(self, chunk_size):
            for index in range(0, len(self.response_content), chunk_size):
                yield self.response_content[index : index + chunk_size]

        def close(self):
            return None

        def release_conn(self):
            return None

    class FakeMinioClient:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        @property
        def base_url(self) -> str:
            if self.endpoint.startswith(("http://", "https://")):
                return self.endpoint
            return f"http://{self.endpoint}"

        def presigned_put_object(self, bucket_name, object_name, expires):
            assert bucket_name == "documents"
            assert expires.total_seconds() == 900
            return f"{self.base_url}/{bucket_name}/{object_name}?X-Amz-Signature=fake"

        def presigned_get_object(self, bucket_name, object_name, expires):
            assert bucket_name == "documents"
            assert expires.total_seconds() == 300
            return f"{self.base_url}/{bucket_name}/{object_name}?X-Amz-Signature=fake-get"

        def stat_object(self, bucket_name, object_name):
            assert bucket_name == "documents"
            if object_exists:
                return {"object_name": object_name}
            raise FakeMissingObjectError()

        def get_object(self, bucket_name, object_name):
            assert bucket_name == "documents"
            if object_exists:
                return FakeMinioResponse(content)
            raise FakeMissingObjectError()

    monkeypatch.setattr(
        DocumentStorageService,
        "_build_minio_client",
        lambda self, raw_endpoint: FakeMinioClient(raw_endpoint),
    )


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
