from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import hash_oauth_value
from app.main import app
from app.models.external_auth_account import ExternalAuthAccount
from app.models.user import User
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.services.account_auth_service import AccountAuthService
from app.services.auth_service import AuthService
from app.services.google_oauth_service import (
    EmailNotVerifiedError,
    GoogleOAuthService,
    GoogleUserProfile,
)

client = TestClient(app)

GOOGLE_AUTH_TABLES = [User.__table__, ExternalAuthAccount.__table__]


@pytest.fixture()
def google_auth_session():
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
    Base.metadata.create_all(engine, tables=GOOGLE_AUTH_TABLES)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine, tables=list(reversed(GOOGLE_AUTH_TABLES)))


def _google_profile(
    *,
    email: str = "ANA@EXAMPLE.COM",
    provider_user_id: str = "google-user-1",
    email_verified: bool = True,
) -> GoogleUserProfile:
    return GoogleUserProfile(
        provider="google",
        provider_user_id=provider_user_id,
        email=email,
        email_verified=email_verified,
        full_name="Ana Gomez",
        first_name="Ana",
        last_name="Gomez",
        avatar_url="https://example.com/avatar.png",
    )


def test_google_login_redirect_creates_state(monkeypatch) -> None:
    created_state: dict = {}

    def fake_create(self, **kwargs):
        created_state.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(OAuthStateRepository, "create", fake_create)

    response = client.get(
        "/api/v1/auth/google/login",
        follow_redirects=False,
    )

    assert response.status_code == 307

    location = response.headers["location"]
    parsed_location = urlparse(location)
    query = parse_qs(parsed_location.query)

    assert parsed_location.scheme == "https"
    assert parsed_location.netloc == "accounts.google.com"
    assert parsed_location.path == "/o/oauth2/v2/auth"
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"][0]
    assert query["nonce"][0]

    assert created_state["provider"] == "google"
    assert created_state["redirect_to"] == "/app/dashboard"
    assert created_state["state_hash"] == hash_oauth_value(query["state"][0])
    assert created_state["nonce_hash"] == hash_oauth_value(query["nonce"][0])


def test_google_login_preserves_unencoded_registration_redirect(monkeypatch) -> None:
    created_state: dict = {}

    def fake_create(self, **kwargs):
        created_state.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(OAuthStateRepository, "create", fake_create)

    response = client.get(
        "/api/v1/auth/google/login"
        "?redirect_to=/verificar-otp?purpose=register&next=/registro?step=datos&auto=1",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert created_state["redirect_to"] == (
        "/verificar-otp?purpose=register&next=/registro?step=datos&auto=1"
    )


def test_google_login_allows_frontend_absolute_redirect(monkeypatch) -> None:
    created_state: dict = {}

    def fake_create(self, **kwargs):
        created_state.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(OAuthStateRepository, "create", fake_create)

    response = client.get(
        "/api/v1/auth/google/login",
        params={"redirect_to": "http://localhost:3000/app/dashboard?tab=home"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert created_state["redirect_to"] == "/app/dashboard?tab=home"


def test_google_login_rejects_external_redirect(monkeypatch) -> None:
    created_state: dict = {}

    def fake_create(self, **kwargs):
        created_state.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(OAuthStateRepository, "create", fake_create)

    response = client.get(
        "/api/v1/auth/google/login",
        params={"redirect_to": "https://evil.example/app/dashboard"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert created_state["redirect_to"] == "/app/dashboard"


def test_complete_google_login_existing_email_verifies_and_links_provider(
    google_auth_session,
) -> None:
    db = google_auth_session
    user = User(
        email="ana@example.com",
        first_name=None,
        last_name=None,
        full_name=None,
        password_hash=None,
        role="user",
        is_active=True,
        is_verified=False,
        status="pending_verification",
    )
    db.add(user)
    db.commit()

    returned_user = AuthService(db).complete_google_login(_google_profile())

    assert returned_user.id == user.id
    assert returned_user.email == "ana@example.com"
    assert returned_user.status == "active"
    assert returned_user.is_verified is True
    assert returned_user.email_verified_at is not None

    account = db.query(ExternalAuthAccount).one()
    assert account.user_id == user.id
    assert account.provider == "google"
    assert account.provider_user_id == "google-user-1"
    assert account.provider_email == "ana@example.com"
    assert account.provider_email_verified is True


def test_complete_google_login_new_user_is_verified_passwordless(
    google_auth_session,
) -> None:
    db = google_auth_session

    user = AuthService(db).complete_google_login(_google_profile())

    assert user.email == "ana@example.com"
    assert user.password_hash is None
    assert user.status == "active"
    assert user.is_verified is True
    assert user.email_verified_at is not None
    assert db.query(ExternalAuthAccount).count() == 1


def test_complete_google_login_rejects_unverified_google_email(
    google_auth_session,
) -> None:
    db = google_auth_session

    with pytest.raises(EmailNotVerifiedError):
        AuthService(db).complete_google_login(
            _google_profile(email_verified=False),
        )

    assert db.query(User).count() == 0


def test_google_callback_invalid_state_redirects_error(monkeypatch) -> None:
    monkeypatch.setattr(
        OAuthStateRepository,
        "get_by_state_hash",
        lambda self, state_hash: None,
    )

    response = client.get(
        "/api/v1/auth/google/callback?code=fake&state=fake",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "http://localhost:3000/auth/login?error=invalid_state"
    )


def test_google_callback_expired_state_redirects_error(monkeypatch) -> None:
    expired_state = SimpleNamespace(
        consumed=False,
        expires_at=datetime.utcnow() - timedelta(minutes=1),
        nonce_hash="unused",
        redirect_to="/dashboard",
    )
    monkeypatch.setattr(
        OAuthStateRepository,
        "get_by_state_hash",
        lambda self, state_hash: expired_state,
    )

    response = client.get(
        "/api/v1/auth/google/callback?code=fake&state=fake",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "http://localhost:3000/auth/login?error=expired_state"
    )


def test_google_callback_success_sets_cookie(monkeypatch) -> None:
    oauth_state = SimpleNamespace(
        consumed=False,
        expires_at=datetime.utcnow() + timedelta(minutes=1),
        nonce_hash=hash_oauth_value("nonce"),
        redirect_to="/app/dashboard",
    )
    user = SimpleNamespace(id=uuid4(), role="user")
    profile = SimpleNamespace(email="user@example.com")

    monkeypatch.setattr(
        OAuthStateRepository,
        "get_by_state_hash",
        lambda self, state_hash: oauth_state,
    )
    monkeypatch.setattr(
        OAuthStateRepository,
        "mark_consumed",
        lambda self, state, consumed_at: state,
    )
    monkeypatch.setattr(
        GoogleOAuthService,
        "exchange_code_for_tokens",
        lambda self, code: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        GoogleOAuthService,
        "validate_id_token",
        lambda self, id_token, expected_nonce_hash=None: {"sub": "google-user"},
    )
    monkeypatch.setattr(
        GoogleOAuthService,
        "normalize_profile",
        lambda self, id_info: profile,
    )
    monkeypatch.setattr(
        AuthService,
        "complete_google_login",
        lambda self, google_profile: user,
    )
    monkeypatch.setattr(
        AccountAuthService,
        "create_session_for_user",
        lambda self, user, ip_address, user_agent: {
            "accessToken": "backend-access-token",
            "refreshToken": "backend-refresh-token",
            "expiresIn": 900,
            "sessionId": str(uuid4()),
            "user": {"requiresOtp": False, "nextStep": "dashboard"},
            "nextStep": "dashboard",
        },
    )

    response = client.get(
        "/api/v1/auth/google/callback?code=valid&state=valid",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "http://localhost:3000/app/dashboard?auth=success&nextStep=dashboard"
    )
    assert "labora_access_token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "fake-id-token" not in response.headers["location"]


def test_google_callback_registration_redirect_does_not_send_otp(monkeypatch) -> None:
    oauth_state = SimpleNamespace(
        consumed=False,
        expires_at=datetime.utcnow() + timedelta(minutes=1),
        nonce_hash=hash_oauth_value("nonce"),
        redirect_to="/verificar-otp?purpose=register&next=/registro?step=datos&auto=1",
    )
    user = SimpleNamespace(id=uuid4(), role="user")
    profile = SimpleNamespace(email="user@example.com")

    monkeypatch.setattr(
        OAuthStateRepository,
        "get_by_state_hash",
        lambda self, state_hash: oauth_state,
    )
    monkeypatch.setattr(
        OAuthStateRepository,
        "mark_consumed",
        lambda self, state, consumed_at: state,
    )
    monkeypatch.setattr(
        GoogleOAuthService,
        "exchange_code_for_tokens",
        lambda self, code: {"id_token": "fake-id-token"},
    )
    monkeypatch.setattr(
        GoogleOAuthService,
        "validate_id_token",
        lambda self, id_token, expected_nonce_hash=None: {"sub": "google-user"},
    )
    monkeypatch.setattr(
        GoogleOAuthService,
        "normalize_profile",
        lambda self, id_info: profile,
    )
    monkeypatch.setattr(
        AuthService,
        "complete_google_login",
        lambda self, google_profile: user,
    )
    monkeypatch.setattr(
        AccountAuthService,
        "create_session_for_user",
        lambda self, user, ip_address, user_agent: {
            "accessToken": "backend-access-token",
            "refreshToken": "backend-refresh-token",
            "expiresIn": 900,
            "sessionId": str(uuid4()),
            "user": {
                "requiresOtp": False,
                "registrationCompleted": False,
                "nextStep": "complete_profile",
            },
            "nextStep": "complete_profile",
        },
    )

    def fail_send_otp(self, user, ip_address, user_agent):
        raise AssertionError("Google OAuth callback must not send OTP")

    monkeypatch.setattr(
        AccountAuthService,
        "send_register_otp_for_user",
        fail_send_otp,
    )

    response = client.get(
        "/api/v1/auth/google/callback?code=valid&state=valid",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed_location = urlparse(location)
    query = parse_qs(parsed_location.query)

    assert parsed_location.geturl().startswith(
        "http://localhost:3000/verificar-otp?"
    )
    assert query["purpose"] == ["register"]
    assert query["next"] == ["/registro?step=datos"]
    assert query["auto"] == ["1"]
    assert query["auth"] == ["success"]
    assert query["nextStep"] == ["complete_profile"]


def test_logout_clears_cookie() -> None:
    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"data": {"loggedOut": True}}
    assert "labora_access_token" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
