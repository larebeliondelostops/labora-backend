import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, unquote_plus, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth_dependencies import (
    get_client_ip,
    get_optional_auth_payload,
    get_user_agent,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import public_rate_limiter
from app.core.security import hash_oauth_value
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    DataResponse,
    ForgotPasswordRequest,
    RefreshTokenRequest,
    ResendOTPRequest,
    ResetPasswordRequest,
    UserLoginRequest,
    UserRegisterRequest,
    VerifyOTPRequest,
)
from app.services.account_auth_service import AccountAuthService
from app.services.auth_service import AuthService, UserDisabledError
from app.services.google_oauth_service import (
    EmailNotVerifiedError,
    GoogleOAuthService,
    InvalidIdTokenError,
    TokenExchangeError,
)

router = APIRouter()


def _safe_relative_redirect(
    redirect_to: str | None,
    default: str = "/app/dashboard",
) -> str:
    if (
        not redirect_to
        or not redirect_to.startswith("/")
        or redirect_to.startswith("//")
        or "\\" in redirect_to
    ):
        return default
    return redirect_to


def _allowed_frontend_origins() -> set[str]:
    origins = set()
    for origin in [settings.frontend_url, *settings.cors_origins]:
        parsed = urlsplit(origin)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return origins


def _safe_redirect_target(
    redirect_to: str | None,
    default: str = "/app/dashboard",
) -> str:
    if not redirect_to:
        return default

    candidate = redirect_to.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in _allowed_frontend_origins():
            return default
        relative_url = urlunsplit(
            ("", "", parsed.path or "/", parsed.query, parsed.fragment)
        )
        return _safe_relative_redirect(relative_url, default)

    return _safe_relative_redirect(candidate, default)


def _redirect_to_from_request(request: Request, redirect_to: str | None) -> str:
    raw_query = request.url.query
    marker = "redirect_to="
    if marker in raw_query:
        raw_redirect = raw_query.split(marker, 1)[1]
        if raw_redirect.startswith("/"):
            return _safe_redirect_target(unquote_plus(raw_redirect))
    return _safe_redirect_target(redirect_to)


def _frontend_url(path: str, params: dict[str, str]) -> str:
    safe_path = _safe_redirect_target(path)
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


def _set_auth_cookie(response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.jwt_access_ttl_seconds,
        httponly=settings.auth_cookie_httponly,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path="/",
    )


def _clear_auth_cookie(response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        domain=settings.auth_cookie_domain,
        path="/",
        samesite=settings.auth_cookie_samesite,
        secure=settings.auth_cookie_secure,
        httponly=settings.auth_cookie_httponly,
    )


def _rate_limit_auth(request: Request, scope: str, identity: str | None = None) -> None:
    ip_address = get_client_ip(request)
    public_rate_limiter.check(
        key=f"auth:{scope}:ip:{ip_address}",
        limit=settings.auth_rate_limit_max_attempts,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    if identity:
        public_rate_limiter.check(
            key=f"auth:{scope}:identity:{identity.lower()}",
            limit=settings.auth_rate_limit_max_attempts,
            window_seconds=settings.auth_rate_limit_window_seconds,
        )


@router.post("/register", response_model=DataResponse, status_code=201)
def register(
    payload: UserRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _rate_limit_auth(request, "register", str(payload.email))
    data = AccountAuthService(db).register(
        payload,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.post("/login", response_model=DataResponse)
def login(
    payload: UserLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    _rate_limit_auth(request, "login", str(payload.email))
    data = AccountAuthService(db).login(
        email=str(payload.email).lower(),
        password=payload.password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    response = JSONResponse({"data": data})
    _set_auth_cookie(response, data["accessToken"])
    return response


@router.post("/verify-otp", response_model=DataResponse)
def verify_otp(
    payload: VerifyOTPRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _rate_limit_auth(request, "verify_otp", payload.recipient)
    data = AccountAuthService(db).verify_otp(
        recipient=payload.recipient,
        purpose=payload.purpose,
        code=payload.code,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.post("/resend-otp", response_model=DataResponse)
def resend_otp(
    payload: ResendOTPRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _rate_limit_auth(request, "resend_otp", payload.recipient)
    data = AccountAuthService(db).resend_otp(
        recipient=payload.recipient,
        purpose=payload.purpose,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.post("/forgot-password", response_model=DataResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _rate_limit_auth(request, "forgot_password", str(payload.email))
    data = AccountAuthService(db).forgot_password(
        email=str(payload.email).lower(),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.post("/reset-password", response_model=DataResponse)
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _rate_limit_auth(request, "reset_password")
    data = AccountAuthService(db).reset_password(
        token=payload.token,
        new_password=payload.new_password,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.post("/refresh", response_model=DataResponse)
def refresh_token(
    payload: RefreshTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    data = AccountAuthService(db).refresh(
        refresh_token=payload.refresh_token,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.post("/logout", response_model=DataResponse)
def logout(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    payload = get_optional_auth_payload(request)
    if payload is None or not payload.get("sid"):
        response = JSONResponse({"data": {"loggedOut": True}})
        _clear_auth_cookie(response)
        return response

    user = UserRepository(db).get_by_id(payload["sub"]) if payload else None
    data = AccountAuthService(db).logout_session(
        session_id=payload.get("sid") if payload else None,
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    response = JSONResponse({"data": data})
    _clear_auth_cookie(response)
    return response


@router.post("/logout-all", response_model=DataResponse)
def logout_all(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    payload = get_optional_auth_payload(request)
    if payload is None:
        from app.core.api_errors import ApiError
        from fastapi import status

        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="No autenticado.",
        )
    user = UserRepository(db).get_by_id(payload["sub"])
    if user is None:
        from app.core.api_errors import ApiError
        from fastapi import status

        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="Sesion invalida.",
        )
    data = AccountAuthService(db).logout_all(
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return {"data": data}


@router.get("/google/login")
def google_login(
    request: Request,
    redirect_to: str | None = Query(default=None),
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
        redirect_to=_redirect_to_from_request(request, redirect_to),
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        expires_at=expires_at,
    )

    authorization_url = GoogleOAuthService().build_authorization_url(
        state=state,
        nonce=nonce,
    )
    return RedirectResponse(authorization_url, status_code=307)


@router.get("/google/callback")
def google_callback(
    request: Request,
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
    except EmailNotVerifiedError:
        return _error_redirect("email_not_verified")
    except UserDisabledError:
        return _error_redirect("user_disabled")

    account_auth = AccountAuthService(db)
    session_data = account_auth.create_session_for_user(
        user=user,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    next_step = _google_callback_next_step(session_data)

    response = RedirectResponse(
        _frontend_url(
            oauth_state.redirect_to,
            {"auth": "success", "nextStep": next_step},
        ),
        status_code=303,
    )
    _set_auth_cookie(response, session_data["accessToken"])
    return response


def _google_callback_next_step(session_data: dict) -> str:
    account_user = session_data.get("user", {})
    next_step = (
        session_data.get("nextStep")
        or account_user.get("nextStep")
        or "dashboard"
    )
    if next_step == "verify_otp":
        return (
            "complete_profile"
            if account_user.get("registrationCompleted") is False
            else "dashboard"
        )
    return next_step
