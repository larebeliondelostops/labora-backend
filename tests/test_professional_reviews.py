from datetime import date
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
from app.models.case import CaseOwner, LaboraCase
from app.models.document import Document
from app.models.payment import Order
from app.models.professional_review import LawyerComment, ProfessionalReview, ReviewedFile, ReviewOrder
from app.models.report import Report
from app.models.user import User
from app.utils.dates import utc_now


def test_client_requests_review_and_duplicate_is_blocked(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    report_id = _create_report(session_factory, user_id=owner_id, case_id=UUID(case_id))

    created = client.post(
        f"/api/v1/cases/{case_id}/professional-review",
        json={
            "targetType": "report",
            "targetId": report_id,
            "reviewType": "report_review",
            "clientNotes": "Quiero revision antes de descargar.",
            "priority": "normal",
        },
        headers=owner_headers,
    )
    duplicate = client.post(
        f"/api/v1/cases/{case_id}/professional-review",
        json={
            "targetType": "report",
            "targetId": report_id,
            "reviewType": "report_review",
        },
        headers=owner_headers,
    )
    listed = client.get("/api/v1/professional-reviews", headers=admin_headers)

    assert created.status_code == 201
    assert created.json()["status"] == "requested"
    assert created.json()["nextAction"] == "wait_assignment"
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "REVIEW_ALREADY_ACTIVE"
    assert listed.status_code == 200
    assert listed.json()["pagination"]["total"] == 1


def test_assignment_comments_and_visibility(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    lawyer_id, lawyer_headers = _create_user(session_factory, role="legal_reviewer")
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    report_id = _create_report(session_factory, user_id=owner_id, case_id=UUID(case_id))
    review_id = _create_review(client, case_id, report_id, owner_headers)

    assigned = client.post(
        f"/api/v1/professional-reviews/{review_id}/assign",
        json={"lawyerId": str(lawyer_id), "assignmentNotes": "Prioridad normal."},
        headers=admin_headers,
    )
    accepted = client.post(
        f"/api/v1/professional-reviews/{review_id}/assignment-response",
        json={"response": "accepted"},
        headers=lawyer_headers,
    )
    internal = client.post(
        f"/api/v1/professional-reviews/{review_id}/comments",
        json={
            "visibility": "internal",
            "commentType": "legal_observation",
            "body": "Nota interna con <script>alert('x')</script>observacion.",
        },
        headers=lawyer_headers,
    )
    visible = client.post(
        f"/api/v1/professional-reviews/{review_id}/comments",
        json={
            "visibility": "client_visible",
            "commentType": "missing_document",
            "body": "Hace falta soporte adicional.",
        },
        headers=lawyer_headers,
    )
    client_detail = client.get(f"/api/v1/professional-reviews/{review_id}", headers=owner_headers)
    lawyer_list = client.get("/api/v1/professional-reviews?status=in_review", headers=lawyer_headers)

    assert assigned.status_code == 200
    assert assigned.json()["lawyerId"] == str(lawyer_id)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "in_review"
    assert internal.status_code == 200
    assert "<script>" not in internal.json()["body"]
    assert visible.status_code == 200
    assert client_detail.status_code == 200
    assert [item["visibility"] for item in client_detail.json()["comments"]] == ["client_visible"]
    assert lawyer_list.status_code == 200
    assert lawyer_list.json()["pagination"]["total"] == 1


def test_paid_review_request_creates_bridge_order(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    report_id = _create_report(session_factory, user_id=owner_id, case_id=UUID(case_id))

    created = client.post(
        f"/api/v1/cases/{case_id}/professional-review",
        json={
            "targetType": "report",
            "targetId": report_id,
            "reviewType": "report_review",
            "requiresPayment": True,
            "amountCop": 123000,
        },
        headers=owner_headers,
    )

    assert created.status_code == 201
    assert created.json()["status"] == "payment_pending"
    assert created.json()["paymentOrderId"] is not None
    assert created.json()["nextAction"] == "pay_review_order"

    db = session_factory()
    try:
        order = db.get(Order, UUID(created.json()["paymentOrderId"]))
        review_order = db.query(ReviewOrder).one()
        assert order is not None
        assert order.product_code == "PROFESSIONAL_REVIEW"
        assert order.total_amount == 123000
        assert review_order.payment_order_id == order.id
        assert review_order.status == "pending"
    finally:
        db.close()


def test_reviewed_file_approval_publishes_final_version(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    lawyer_id, lawyer_headers = _create_user(session_factory, role="legal_reviewer")
    _admin_id, admin_headers = _create_user(session_factory, role="legal_admin")
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    report_id = _create_report(session_factory, user_id=owner_id, case_id=UUID(case_id))
    document_id = _create_document(session_factory, user_id=owner_id, case_id=UUID(case_id))
    review_id = _create_review(client, case_id, report_id, owner_headers)
    client.post(
        f"/api/v1/professional-reviews/{review_id}/assign",
        json={"lawyerId": str(lawyer_id)},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/professional-reviews/{review_id}/assignment-response",
        json={"response": "accepted"},
        headers=lawyer_headers,
    )

    reviewed_file = client.post(
        f"/api/v1/professional-reviews/{review_id}/reviewed-files",
        json={
            "uploadedFileId": document_id,
            "fileType": "reviewed_report_pdf",
            "status": "ready_for_approval",
        },
        headers=lawyer_headers,
    )
    approved = client.post(
        f"/api/v1/professional-reviews/{review_id}/approve",
        json={
            "reviewedFileId": reviewed_file.json()["id"],
            "approvalNote": "Documento revisado y aprobado.",
            "publishToClient": True,
        },
        headers=lawyer_headers,
    )

    assert reviewed_file.status_code == 200
    assert reviewed_file.json()["versionNumber"] == 1
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"

    db = session_factory()
    try:
        review = db.get(ProfessionalReview, UUID(review_id))
        report = db.get(Report, UUID(report_id))
        final_file = db.get(ReviewedFile, UUID(reviewed_file.json()["id"]))
        audit_names = {event.event_type for event in db.query(AuditEvent).all()}
        assert review is not None
        assert review.status == "completed"
        assert report is not None
        assert report.status == "approved"
        assert final_file is not None
        assert final_file.status == "published"
        assert final_file.approved_by == lawyer_id
        assert "revision_profesional.file_reviewed" in audit_names
        assert "revision_profesional.approved" in audit_names
        assert "revision_profesional.completed" in audit_names
    finally:
        db.close()


def test_client_cannot_create_internal_comment(client_and_session) -> None:
    client, session_factory = client_and_session
    owner_id, owner_headers = _create_user(session_factory)
    case_id = _create_case_row(session_factory, owner_id, status="completed")
    report_id = _create_report(session_factory, user_id=owner_id, case_id=UUID(case_id))
    review_id = _create_review(client, case_id, report_id, owner_headers)

    forbidden = client.post(
        f"/api/v1/professional-reviews/{review_id}/comments",
        json={"visibility": "internal", "commentType": "general", "body": "No deberia."},
        headers=owner_headers,
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "COMMENT_VISIBILITY_FORBIDDEN"

    db = session_factory()
    try:
        assert db.query(LawyerComment).count() == 0
    finally:
        db.close()


def _create_review(client: TestClient, case_id: str, report_id: str, headers: dict[str, str]) -> str:
    response = client.post(
        f"/api/v1/cases/{case_id}/professional-review",
        json={"targetType": "report", "targetId": report_id, "reviewType": "report_review"},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


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
        token = create_access_token(str(user.id), {"role": role})
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
            next_best_action="generate_legal_action",
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


def _create_report(session_factory, *, user_id: UUID, case_id: UUID) -> str:
    db = session_factory()
    now = utc_now()
    try:
        report = Report(
            case_id=case_id,
            owner_user_id=user_id,
            report_type="full",
            title="Informe completo",
            status="ready",
            language="es-CO",
            visibility="user",
            requires_human_review=False,
            generated_by="deterministic",
            created_at=now,
            updated_at=now,
        )
        db.add(report)
        db.commit()
        return str(report.id)
    finally:
        db.close()


def _create_document(session_factory, *, user_id: UUID, case_id: UUID) -> str:
    db = session_factory()
    now = utc_now()
    try:
        document = Document(
            case_id=case_id,
            uploaded_by_user_id=user_id,
            original_filename="revision.pdf",
            display_name="revision.pdf",
            mime_type="application/pdf",
            extension="pdf",
            size_bytes=1200,
            storage_bucket="documents",
            storage_key=f"cases/{case_id}/revision.pdf",
            sha256_hash="a" * 64,
            status="validated",
            validation_status="passed",
            classification_source="user",
            is_primary=False,
            is_duplicate=False,
            is_password_protected=False,
            is_corrupted=False,
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.commit()
        return str(document.id)
    finally:
        db.close()


@pytest.fixture()
def client_and_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
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
