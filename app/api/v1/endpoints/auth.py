import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, get_password_hash, hash_oauth_value
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.schemas.auth import LogoutResponse
from app.schemas.user import UserCreate, UserLogin
from app.services.auth_service import AuthService, UserDisabledError
from app.services.google_oauth_service import (
    EmailNotVerifiedError,
    GoogleOAuthService,
    InvalidIdTokenError,
    TokenExchangeError,
)

router = APIRouter()


def _safe_redirect_path(redirect_to: str | None, default: str = "/dashboard") -> str:
    if not redirect_to or not redirect_to.startswith("/") or redirect_to.startswith("//"):
        return default
    return redirect_to


def _frontend_url(path: str, params: dict[str, str]) -> str:
    safe_path = _safe_redirect_path(path)
    parsed = urlsplit(safe_path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    relative_url = urlunsplit(("", "", parsed.path, urlencode(query), parsed.fragment))
    return f"{settings.frontend_url}{relative_url}"


def _error_redirect(error_code: str) -> RedirectResponse:
    return RedirectResponse(
        _frontend_url("/auth/login", {"error": error_code}),
        status_code=303,
    )


def _set_auth_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=settings.auth_cookie_httponly,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path="/",
    )


@router.post("/register")
def register(payload: UserCreate) -> dict[str, str]:
    return {
        "message": "Registration scaffold ready",
        "password_hash_preview": get_password_hash(payload.password)[:20],
    }


@router.post("/login")
def login(payload: UserLogin) -> dict[str, str]:
    return {
        "access_token": create_access_token(payload.email),
        "token_type": "bearer",
    }


@router.get("/google/login")
def google_login(
    request: Request,
    redirect_to: str = Query(default="/dashboard"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(
        minutes=settings.oauth_state_expire_minutes
    )

    OAuthStateRepository(db).create(
        state_hash=hash_oauth_value(state),
        nonce_hash=hash_oauth_value(nonce),
        provider="google",
        redirect_to=_safe_redirect_path(redirect_to),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        expires_at=expires_at,
    )

    authorization_url = GoogleOAuthService().build_authorization_url(
        state=state,
        nonce=nonce,
    )
    return RedirectResponse(authorization_url, status_code=307)


@router.get("/google/callback")
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        if error == "access_denied":
            return _error_redirect("google_auth_cancelled")
        return _error_redirect("google_auth_failed")

    if not code:
        return _error_redirect("missing_code")
    if not state:
        return _error_redirect("invalid_state")

    state_repository = OAuthStateRepository(db)
    oauth_state = state_repository.get_by_state_hash(hash_oauth_value(state))
    if oauth_state is None:
        return _error_redirect("invalid_state")
    if oauth_state.consumed:
        return _error_redirect("used_state")
    if oauth_state.expires_at <= datetime.utcnow():
        return _error_redirect("expired_state")

    state_repository.mark_consumed(oauth_state, datetime.utcnow())

    google_oauth = GoogleOAuthService()
    try:
        token_data = google_oauth.exchange_code_for_tokens(code)
    except TokenExchangeError:
        return _error_redirect("token_exchange_failed")

    try:
        id_info = google_oauth.validate_id_token(
            token_data["id_token"],
            expected_nonce_hash=oauth_state.nonce_hash,
        )
        profile = google_oauth.normalize_profile(id_info)
    except EmailNotVerifiedError:
        return _error_redirect("email_not_verified")
    except InvalidIdTokenError:
        return _error_redirect("invalid_id_token")

    try:
        user = AuthService(db).complete_google_login(profile)
    except UserDisabledError:
        return _error_redirect("user_disabled")

    access_token = create_access_token(str(user.id), {"role": user.role})
    response = RedirectResponse(
        _frontend_url(oauth_state.redirect_to, {"auth": "success"}),
        status_code=303,
    )
    _set_auth_cookie(response, access_token)
    return response


@router.post("/logout", response_model=LogoutResponse)
def logout() -> JSONResponse:
    response = JSONResponse({"message": "Logged out successfully"})
    response.delete_cookie(
        key=settings.auth_cookie_name,
        domain=settings.auth_cookie_domain,
        path="/",
        samesite=settings.auth_cookie_samesite,
        secure=settings.auth_cookie_secure,
        httponly=settings.auth_cookie_httponly,
    )
    return response


@router.post("/verify")
def verify() -> dict[str, str]:
    return {"message": "Verification scaffold ready"}
