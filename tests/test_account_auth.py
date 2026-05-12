from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.api_errors import ApiError
from app.core.rate_limit import public_rate_limiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_otp_code,
    validate_password_strength,
    verify_otp_code,
)
from app.main import app
from app.services.account_auth_service import AccountAuthService

client = TestClient(app)


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


def test_register_duplicate_email_error(monkeypatch) -> None:
    public_rate_limiter.clear()

    def fake_register(self, payload, *, ip_address, user_agent):
        raise ApiError(
            status_code=409,
            code="EMAIL_ALREADY_EXISTS",
            message="El correo ya esta registrado.",
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
