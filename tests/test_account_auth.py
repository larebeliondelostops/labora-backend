from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.api_errors import ApiError
from app.core.database import Base, get_db
from app.core.rate_limit import public_rate_limiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_otp_code,
    validate_password_strength,
    verify_otp_code,
)
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.otp_code import OTPCode
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import UserRegisterRequest
from app.schemas.user import UserProfileUpdate
from app.services.account_auth_service import AccountAuthService
from app.services.email_service import EmailService

client = TestClient(app)


AUTH_TABLES = [User.__table__, OTPCode.__table__, AuditEvent.__table__]


@pytest.fixture()
def auth_client_and_session():
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
    Base.metadata.create_all(engine, tables=AUTH_TABLES)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield client, TestingSessionLocal
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine, tables=list(reversed(AUTH_TABLES)))


def _register_payload(
    *,
    email: str = "ana@example.com",
    document_number: str = "1020304050",
) -> dict:
    return {
        "firstName": "Ana",
        "lastName": "Gomez",
        "documentType": "CC",
        "documentNumber": document_number,
        "email": email,
        "password": "Password123*",
    }


def test_password_strength_validation() -> None:
    validate_password_strength("Password123*")

    try:
        validate_password_strength("weak")
    except ValueError as exc:
        assert "10 caracteres" in str(exc)
    else:
        raise AssertionError("Weak password should fail")


def test_otp_hash_and_verify() -> None:
    code_hash = hash_otp_code("123456")

    assert verify_otp_code("123456", code_hash)
    assert not verify_otp_code("000000", code_hash)


def test_access_token_roundtrip() -> None:
    user_id = str(uuid4())
    token = create_access_token(user_id, {"role": "user", "sid": str(uuid4())})

    payload = decode_access_token(token)

    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["type"] == "access"


def test_pending_google_account_user_points_front_to_otp() -> None:
    user = SimpleNamespace(
        id=uuid4(),
        first_name="Jorge",
        last_name="Hernandez",
        full_name="Jorge Hernandez",
        avatar_url="https://example.com/avatar.png",
        document_type=None,
        document_number=None,
        phone=None,
        email="jorge@example.com",
        status="pending_verification",
        email_verified_at=None,
        is_verified=False,
        phone_verified_at=None,
        role="user",
        created_at=None,
    )

    account_user = AccountAuthService(db=None)._account_user(user)
    data = account_user.model_dump(by_alias=True)

    assert data["requiresOtp"] is True
    assert data["registrationCompleted"] is False
    assert data["nextStep"] == "verify_otp"


def test_google_profile_creates_pending_passwordless_user() -> None:
    class FakeDb:
        def add(self, item):
            self.item = item

        def flush(self):
            return None

    profile = SimpleNamespace(
        email="jorge@example.com",
        first_name="Jorge",
        last_name="Hernandez",
        full_name="Jorge Hernandez",
        avatar_url="https://example.com/avatar.png",
        email_verified=True,
    )

    user = UserRepository(FakeDb()).create_from_google_profile(profile)

    assert user.email == "jorge@example.com"
    assert user.password_hash is None
    assert user.status == "pending_verification"
    assert user.is_verified is False


def test_verified_google_account_without_document_points_front_to_profile() -> None:
    user = SimpleNamespace(
        id=uuid4(),
        first_name="Jorge",
        last_name="Hernandez",
        full_name="Jorge Hernandez",
        avatar_url=None,
        document_type=None,
        document_number=None,
        phone=None,
        email="jorge@example.com",
        status="active",
        email_verified_at=datetime.utcnow(),
        is_verified=True,
        phone_verified_at=None,
        role="user",
        created_at=None,
    )

    account_user = AccountAuthService(db=None)._account_user(user)
    data = account_user.model_dump(by_alias=True)

    assert data["requiresOtp"] is False
    assert data["registrationCompleted"] is False
    assert data["nextStep"] == "complete_profile"


def test_profile_update_accepts_registration_fields() -> None:
    payload = UserProfileUpdate.model_validate(
        {
            "documentType": "cc",
            "documentNumber": " 1.023.abc ",
            "phone": "+57 300 111 2233",
        }
    )

    assert payload.document_type == "CC"
    assert payload.document_number == "1023ABC"
    assert payload.phone == "+573001112233"


def test_register_endpoint_contract(monkeypatch) -> None:
    public_rate_limiter.clear()

    def fake_register(self, payload, *, ip_address, user_agent):
        assert str(payload.email) == "ana@example.com"
        assert payload.document_type == "CC"
        return {
            "user": {
                "id": str(uuid4()),
                "firstName": payload.first_name,
                "lastName": payload.last_name,
                "documentType": payload.document_type,
                "documentNumberMasked": "******4050",
                "email": str(payload.email),
                "phoneMasked": "+57******2233",
                "status": "pending_verification",
                "emailVerified": False,
                "phoneVerified": False,
                "roles": ["user"],
                "createdAt": None,
            },
            "nextStep": "verify_otp",
        }

    monkeypatch.setattr(AccountAuthService, "register", fake_register)

    response = client.post(
        "/api/v1/auth/register",
        json={
            "firstName": "Ana Maria",
            "lastName": "Gomez Rojas",
            "documentType": "cc",
            "documentNumber": "1020304050",
            "email": "ANA@example.com",
            "phone": "+57 300 111 2233",
            "password": "Password123*",
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["nextStep"] == "verify_otp"
    assert response.json()["data"]["user"]["documentNumberMasked"] == "******4050"


def test_register_new_user_creates_otp(auth_client_and_session, monkeypatch) -> None:
    client, session_factory = auth_client_and_session
    sent_otps = []

    def fake_send_otp(self, *, recipient, code, purpose):
        sent_otps.append(
            {
                "recipient": recipient,
                "code": code,
                "purpose": purpose,
            }
        )

    monkeypatch.setattr(EmailService, "send_otp", fake_send_otp)

    response = client.post(
        "/api/v1/auth/register",
        json=_register_payload(
            email="ANA@EXAMPLE.COM",
            document_number="1.020.304.050",
        ),
    )

    assert response.status_code == 201
    assert response.json()["data"]["nextStep"] == "verify_otp"
    assert len(sent_otps) == 1
    assert sent_otps[0]["recipient"] == "ana@example.com"
    assert sent_otps[0]["purpose"] == "register"
    assert len(sent_otps[0]["code"]) == 6

    with session_factory() as db:
        user = db.query(User).filter(User.email == "ana@example.com").one()
        assert user.document_number == "1020304050"
        assert db.query(OTPCode).count() == 1


def test_register_duplicate_email_blocks_user_and_otp(
    auth_client_and_session,
    monkeypatch,
) -> None:
    client, session_factory = auth_client_and_session
    sent_otps = []
    monkeypatch.setattr(
        EmailService,
        "send_otp",
        lambda self, **kwargs: sent_otps.append(kwargs),
    )

    first = client.post("/api/v1/auth/register", json=_register_payload())
    duplicate = client.post(
        "/api/v1/auth/register",
        json=_register_payload(
            email=" ANA@EXAMPLE.COM ",
            document_number="999888777",
        ),
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    error = duplicate.json()["error"]
    assert error["code"] == "EMAIL_ALREADY_EXISTS"
    assert error["details"][0]["field"] == "email"
    assert error["details"][0]["nextStep"] == "login"
    assert error["details"][0]["redirectTo"] == "/auth/login"

    with session_factory() as db:
        assert db.query(User).count() == 1
        assert db.query(OTPCode).count() == 1
    assert len(sent_otps) == 1


def test_register_duplicate_document_blocks_user_and_otp(
    auth_client_and_session,
    monkeypatch,
) -> None:
    client, session_factory = auth_client_and_session
    sent_otps = []
    monkeypatch.setattr(
        EmailService,
        "send_otp",
        lambda self, **kwargs: sent_otps.append(kwargs),
    )

    first = client.post("/api/v1/auth/register", json=_register_payload())
    duplicate = client.post(
        "/api/v1/auth/register",
        json=_register_payload(
            email="otra@example.com",
            document_number="1.020.304.050",
        ),
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    error = duplicate.json()["error"]
    assert error["code"] == "DOCUMENT_ALREADY_EXISTS"
    assert error["details"][0]["field"] == "documentNumber"
    assert error["details"][0]["nextStep"] == "login"
    assert error["details"][0]["redirectTo"] == "/auth/login"

    with session_factory() as db:
        assert db.query(User).count() == 1
        assert db.query(OTPCode).count() == 1
    assert len(sent_otps) == 1


@pytest.mark.parametrize(
    ("constraint_name", "expected_code"),
    [
        ("ix_users_email", "EMAIL_ALREADY_EXISTS"),
        ("uq_users_document", "DOCUMENT_ALREADY_EXISTS"),
    ],
)
def test_register_maps_unique_race_to_conflict(
    constraint_name,
    expected_code,
) -> None:
    class FakeUsers:
        def get_by_email(self, email):
            return None

        def get_by_document(self, document_type, document_number):
            return None

        def create_from_registration(self, payload):
            orig = SimpleNamespace(
                diag=SimpleNamespace(constraint_name=constraint_name)
            )
            raise IntegrityError("insert users", {}, orig)

    class FakeDb:
        def __init__(self):
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    db = FakeDb()
    service = AccountAuthService(db)
    service.users = FakeUsers()

    try:
        service.register(
            UserRegisterRequest.model_validate(_register_payload()),
            ip_address=None,
            user_agent=None,
        )
    except ApiError as exc:
        assert exc.status_code == 409
        assert exc.code == expected_code
        assert exc.details[0]["nextStep"] == "login"
        assert db.rollbacks == 1
    else:
        raise AssertionError("Unique constraint violations should map to API conflicts")


def test_register_duplicate_email_error(monkeypatch) -> None:
    public_rate_limiter.clear()

    def fake_register(self, payload, *, ip_address, user_agent):
        raise ApiError(
            status_code=409,
            code="EMAIL_ALREADY_EXISTS",
            message="El correo ya esta registrado. Inicia sesion para continuar.",
            details=[{"nextStep": "login", "redirectTo": "/auth/login"}],
        )

    monkeypatch.setattr(AccountAuthService, "register", fake_register)

    response = client.post(
        "/api/v1/auth/register",
        json={
            "firstName": "Ana",
            "lastName": "Gomez",
            "documentType": "CC",
            "documentNumber": "1020304050",
            "email": "ana@example.com",
            "password": "Password123*",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    assert response.json()["error"]["details"][0]["nextStep"] == "login"


def test_register_existing_email_points_front_to_login() -> None:
    class FakeUsers:
        def get_by_email(self, email):
            assert email == "ana@example.com"
            return object()

    class FakeDb:
        pass

    payload = UserRegisterRequest.model_validate(
        {
            "firstName": "Ana",
            "lastName": "Gomez",
            "documentType": "CC",
            "documentNumber": "1020304050",
            "email": "ana@example.com",
            "password": "Password123*",
        }
    )
    service = AccountAuthService(FakeDb())
    service.users = FakeUsers()

    try:
        service.register(payload, ip_address=None, user_agent=None)
    except ApiError as exc:
        assert exc.status_code == 409
        assert exc.code == "EMAIL_ALREADY_EXISTS"
        assert exc.details[0]["nextStep"] == "login"
        assert exc.details[0]["redirectTo"] == "/auth/login"
    else:
        raise AssertionError("Existing email should point the frontend to login")


def test_login_unregistered_email_points_front_to_register() -> None:
    class FakeUsers:
        def get_by_email(self, email):
            assert email == "nadie@example.com"
            return None

    class FakeAuditEvents:
        def __init__(self):
            self.events = []

        def create(self, **kwargs):
            self.events.append(kwargs)

    class FakeDb:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    db = FakeDb()
    audit_events = FakeAuditEvents()
    service = AccountAuthService(db)
    service.users = FakeUsers()
    service.audit_events = audit_events

    try:
        service.login(
            email="nadie@example.com",
            password="Password123*",
            ip_address=None,
            user_agent=None,
        )
    except ApiError as exc:
        assert exc.status_code == 404
        assert exc.code == "USER_NOT_REGISTERED"
        assert exc.details[0]["nextStep"] == "register"
        assert exc.details[0]["redirectTo"] == "/registro"
        assert audit_events.events[0]["metadata"]["reason"] == "user_not_registered"
        assert db.commits == 1
    else:
        raise AssertionError("Unregistered email should point the frontend to register")


def test_login_endpoint_sets_cookie(monkeypatch) -> None:
    public_rate_limiter.clear()

    def fake_login(self, *, email, password, ip_address, user_agent):
        return {
            "accessToken": create_access_token(str(uuid4()), {"role": "user", "sid": str(uuid4())}),
            "refreshToken": "refresh-token-value",
            "expiresIn": 900,
            "user": {
                "id": str(uuid4()),
                "firstName": "Ana",
                "lastName": "Gomez",
                "email": email,
                "status": "active",
                "emailVerified": True,
                "roles": ["user"],
            },
            "nextStep": "dashboard",
        }

    monkeypatch.setattr(AccountAuthService, "login", fake_login)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "ana@example.com", "password": "Password123*"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["refreshToken"] == "refresh-token-value"
    assert "labora_access_token" in response.headers["set-cookie"]


def test_verify_otp_endpoint_contract(monkeypatch) -> None:
    public_rate_limiter.clear()

    monkeypatch.setattr(
        AccountAuthService,
        "verify_otp",
        lambda self, **kwargs: {
            "verified": True,
            "userStatus": "active",
            "nextStep": "consents",
        },
    )

    response = client.post(
        "/api/v1/auth/verify-otp",
        json={"recipient": "ana@example.com", "purpose": "register", "code": "123456"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["nextStep"] == "consents"


def test_verify_otp_rejects_phone_recipient() -> None:
    public_rate_limiter.clear()

    response = client.post(
        "/api/v1/auth/verify-otp",
        json={"recipient": "+573001112233", "purpose": "register", "code": "123456"},
    )

    assert response.status_code == 422


def test_verify_otp_rejects_phone_change_purpose() -> None:
    public_rate_limiter.clear()

    response = client.post(
        "/api/v1/auth/verify-otp",
        json={
            "recipient": "ana@example.com",
            "purpose": "phone_change",
            "code": "123456",
        },
    )

    assert response.status_code == 422


def test_forgot_password_does_not_reveal_existence(monkeypatch) -> None:
    public_rate_limiter.clear()

    monkeypatch.setattr(
        AccountAuthService,
        "forgot_password",
        lambda self, **kwargs: {
            "message": "Si el correo existe, enviaremos instrucciones para restablecer la contrasena."
        },
    )

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "nadie@example.com"},
    )

    assert response.status_code == 200
    assert "Si el correo existe" in response.json()["data"]["message"]


def test_refresh_endpoint_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        AccountAuthService,
        "refresh",
        lambda self, **kwargs: {
            "accessToken": "new-access-token",
            "refreshToken": "new-refresh-token",
            "expiresIn": 900,
        },
    )

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refreshToken": "opaque-refresh-token-value"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["accessToken"] == "new-access-token"
