from datetime import date, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import app.models  # noqa: F401
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.rate_limit import public_rate_limiter
from app.core.security import create_access_token
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.case import CaseOwner, LaboraCase
from app.models.delivery import DeliveryEvent, DeliveryPackage, DownloadFile, ShareLink
from app.models.professional_review import ProfessionalReview
from app.models.user import User
from app.services.document_storage_service import DocumentStorageService
from app.utils.dates import utc_now


def test_delivery_center_lists_files_without_download_urls(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    package_id, available_file_id = _create_delivery_package(
        session_factory,
        owner_id=owner_id,
        case_id=UUID(case_id),
    )
    _create_download_file(
        session_factory,
        package_id=package_id,
        case_id=UUID(case_id),
        status="locked",
        is_unlocked=False,
        file_name="bloqueado.pdf",
    )

    response = client.get(f"/api/v1/cases/{case_id}/delivery", headers=owner_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["package"]["status"] == "ready"
    assert {item["id"] for item in payload["files"]} >= {str(available_file_id)}
    assert all("downloadUrl" not in item for item in payload["files"])
    assert payload["availableActions"]["canDownload"] is True


def test_download_available_file_returns_signed_url_and_audits(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _package_id, file_id = _create_delivery_package(
        session_factory,
        owner_id=owner_id,
        case_id=UUID(case_id),
    )

    response = client.get(f"/api/v1/files/{file_id}/download", headers=owner_headers)
    parsed = urlsplit(response.json()["downloadUrl"])
    streamed = client.get(f"{parsed.path}?{parsed.query}")

    assert response.status_code == 200
    assert response.json()["expiresInSeconds"] == settings.delivery_signed_url_ttl_seconds
    assert streamed.status_code == 200
    assert streamed.content == b"delivery-pdf-content"

    db = session_factory()
    try:
        file = db.get(DownloadFile, file_id)
        assert file is not None
        assert file.download_count == 1
        event_types = {item.event_type for item in db.query(DeliveryEvent).all()}
        assert "entrega_final.file_download_requested" in event_types
        assert "entrega_final.file_downloaded" in event_types
        audit_types = {item.event_type for item in db.query(AuditEvent).all()}
        assert "entrega_final.file_downloaded" in audit_types
    finally:
        db.close()


def test_locked_file_cannot_be_downloaded(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    package_id, _file_id = _create_delivery_package(
        session_factory,
        owner_id=owner_id,
        case_id=UUID(case_id),
    )
    locked_file_id = _create_download_file(
        session_factory,
        package_id=package_id,
        case_id=UUID(case_id),
        status="locked",
        is_unlocked=False,
        file_name="locked.pdf",
    )

    response = client.get(f"/api/v1/files/{locked_file_id}/download", headers=owner_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DELIVERY_FILE_LOCKED"


def test_foreign_user_cannot_view_delivery_center(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _other_id, other_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _create_delivery_package(session_factory, owner_id=owner_id, case_id=UUID(case_id))

    response = client.get(f"/api/v1/cases/{case_id}/delivery", headers=other_headers)

    assert owner_headers != other_headers
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DELIVERY_FORBIDDEN"


def test_share_link_exposes_only_allowed_files_and_can_be_revoked(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    package_id, allowed_file_id = _create_delivery_package(
        session_factory,
        owner_id=owner_id,
        case_id=UUID(case_id),
    )
    _other_file_id = _create_download_file(
        session_factory,
        package_id=package_id,
        case_id=UUID(case_id),
        file_name="no-compartido.pdf",
    )

    created = client.post(
        f"/api/v1/cases/{case_id}/share-links",
        json={
            "recipientName": "Abogada Ana",
            "recipientEmail": "ana@example.com",
            "permissions": ["view", "download"],
            "allowedFileIds": [str(allowed_file_id)],
            "expiresAt": (utc_now() + timedelta(days=2)).isoformat(),
            "maxViews": 5,
        },
        headers=owner_headers,
    )
    token = created.json()["shareUrl"].rsplit("/", 1)[1]
    shared = client.get(f"/api/v1/share/delivery/{token}")
    downloaded = client.get(f"/api/v1/share/delivery/{token}/files/{allowed_file_id}/download")
    revoked = client.delete(
        f"/api/v1/cases/{case_id}/share-links/{created.json()['id']}",
        headers=owner_headers,
    )
    after_revoke = client.get(f"/api/v1/share/delivery/{token}")

    assert created.status_code == 200
    assert shared.status_code == 200
    assert [item["id"] for item in shared.json()["files"]] == [str(allowed_file_id)]
    assert shared.json()["package"]["ownerDisplayName"] == "Cliente Labora"
    assert downloaded.status_code == 200
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert after_revoke.status_code == 403
    assert after_revoke.json()["error"]["code"] == "SHARE_LINK_REVOKED"

    db = session_factory()
    try:
        share = db.get(ShareLink, UUID(created.json()["id"]))
        assert share is not None
        assert token not in share.token_hash
    finally:
        db.close()


def test_share_link_requires_future_expiration(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _create_delivery_package(session_factory, owner_id=owner_id, case_id=UUID(case_id))

    response = client.post(
        f"/api/v1/cases/{case_id}/share-links",
        json={
            "recipientEmail": "ana@example.com",
            "permissions": ["view"],
            "expiresAt": (utc_now() - timedelta(minutes=1)).isoformat(),
        },
        headers=owner_headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SHARE_LINK_INVALID"


def test_close_case_blocks_pending_review_and_success_preserves_files(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    package_id, _file_id = _create_delivery_package(
        session_factory,
        owner_id=owner_id,
        case_id=UUID(case_id),
    )
    _create_professional_review(session_factory, owner_id=owner_id, case_id=UUID(case_id))

    blocked = client.post(
        f"/api/v1/cases/{case_id}/close",
        json={"reason": "user_completed_download"},
        headers=owner_headers,
    )
    _complete_reviews(session_factory, case_id=UUID(case_id))
    closed = client.post(
        f"/api/v1/cases/{case_id}/close",
        json={
            "reason": "user_completed_download",
            "notes": "Ya descargue el expediente.",
        },
        headers=owner_headers,
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "CASE_CANNOT_BE_CLOSED"
    assert closed.status_code == 200
    assert closed.json()["closureStatus"] == "closed"

    db = session_factory()
    try:
        assert db.query(DownloadFile).filter(DownloadFile.delivery_package_id == package_id).count() == 1
        package = db.get(DeliveryPackage, package_id)
        assert package is not None
        assert package.status == "closed"
        event_types = {item.event_type for item in db.query(DeliveryEvent).all()}
        assert "entrega_final.closed" in event_types
    finally:
        db.close()


def test_low_confidence_ai_summary_marks_package_for_review(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, _owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    _create_empty_delivery_package(session_factory, owner_id=owner_id, case_id=UUID(case_id))

    response = client.post(
        f"/api/v1/cases/{case_id}/delivery/ai-summary",
        json={"force": True},
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AI_SUMMARY_LOW_CONFIDENCE"

    db = session_factory()
    try:
        package = db.query(DeliveryPackage).filter(DeliveryPackage.case_id == UUID(case_id)).one()
        assert package.status == "requires_review"
        assert package.ai_summary is not None
    finally:
        db.close()


def _create_user(session_factory, *, role: str = "user"):
    db = session_factory()
    try:
        user = User(
            email=f"{uuid4().hex}@example.com",
            password_hash="hashed",
            first_name="Maria",
            last_name="Gomez",
            full_name="Maria Gomez",
            role=role,
            is_active=True,
            is_verified=True,
            status="active",
        )
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id))
        return user.id, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def _create_case_row(session_factory, user_id: UUID, *, status: str) -> str:
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
            status=status,
            current_step="completed",
            next_best_action="view_delivery",
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


def _create_delivery_package(session_factory, *, owner_id: UUID, case_id: UUID):
    package_id = _create_empty_delivery_package(session_factory, owner_id=owner_id, case_id=case_id)
    file_id = _create_download_file(
        session_factory,
        package_id=package_id,
        case_id=case_id,
        file_name="informe-tecnico.pdf",
    )
    return package_id, file_id


def _create_empty_delivery_package(session_factory, *, owner_id: UUID, case_id: UUID) -> UUID:
    db = session_factory()
    now = utc_now()
    try:
        package = DeliveryPackage(
            case_id=case_id,
            owner_user_id=owner_id,
            status="ready",
            title="Entrega final del expediente",
            description="Documentos finales generados para el caso.",
            version=1,
            is_latest=True,
            unlocked_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(package)
        db.commit()
        return package.id
    finally:
        db.close()


def _create_download_file(
    session_factory,
    *,
    package_id: UUID,
    case_id: UUID,
    file_name: str,
    status: str = "available",
    is_unlocked: bool = True,
) -> UUID:
    storage_key = f"delivery/{case_id}/{uuid4().hex}-{file_name}"
    DocumentStorageService().save(
        storage_key=storage_key,
        content=b"delivery-pdf-content",
        content_type="application/pdf",
    )
    db = session_factory()
    now = utc_now()
    try:
        file = DownloadFile(
            delivery_package_id=package_id,
            case_id=case_id,
            file_storage_key=storage_key,
            file_name=file_name,
            file_type="pdf",
            mime_type="application/pdf",
            size_bytes=len(b"delivery-pdf-content"),
            checksum_sha256="a" * 64,
            category="technical_report",
            status=status,
            is_unlocked=is_unlocked,
            requires_review=False,
            version=1,
            download_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(file)
        db.commit()
        return file.id
    finally:
        db.close()


def _create_professional_review(session_factory, *, owner_id: UUID, case_id: UUID) -> None:
    db = session_factory()
    now = utc_now()
    try:
        db.add(
            ProfessionalReview(
                case_id=case_id,
                client_id=owner_id,
                requested_by=owner_id,
                status="in_review",
                review_type="legal",
                target_type="delivery_package",
                target_id=uuid4(),
                priority="normal",
                requires_payment=False,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


def _complete_reviews(session_factory, *, case_id: UUID) -> None:
    db = session_factory()
    try:
        for review in db.query(ProfessionalReview).filter(ProfessionalReview.case_id == case_id).all():
            review.status = "completed"
            review.completed_at = utc_now()
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def client_and_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "DELIVERY_SHARE_BASE_URL", "https://labora.test/share/delivery")
    public_rate_limiter.clear()
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
    public_rate_limiter.clear()
