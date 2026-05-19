from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings
from app.core.security import hash_oauth_value


class GoogleOAuthError(Exception):
    """Base exception for Google OAuth failures."""


class TokenExchangeError(GoogleOAuthError):
    pass


class InvalidIdTokenError(GoogleOAuthError):
    pass


class EmailNotVerifiedError(GoogleOAuthError):
    pass


@dataclass(frozen=True)
class GoogleUserProfile:
    provider: str
    provider_user_id: str
    email: str
    email_verified: bool
    full_name: str | None
    first_name: str | None
    last_name: str | None
    avatar_url: str | None


class GoogleOAuthService:
    AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

    def build_authorization_url(self, *, state: str, nonce: str) -> str:
        params = {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": settings.google_oauth_scopes,
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        return f"{self.AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    def exchange_code_for_tokens(self, code: str) -> dict:
        payload = {
            "code": code,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    self.TOKEN_ENDPOINT,
                    data=payload,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TokenExchangeError("Google token exchange failed") from exc

        token_data = response.json()
        if not token_data.get("id_token"):
            raise TokenExchangeError("Google token response did not include id_token")
        return token_data

    def validate_id_token(
        self,
        id_token_value: str,
        *,
        expected_nonce_hash: str | None = None,
    ) -> dict:
        try:
            id_info = google_id_token.verify_oauth2_token(
                id_token_value,
                google_requests.Request(),
                settings.google_oauth_client_id,
            )
        except ValueError as exc:
            raise InvalidIdTokenError("Invalid Google id_token") from exc

        if id_info.get("iss") not in self.VALID_ISSUERS:
            raise InvalidIdTokenError("Invalid Google issuer")

        if not id_info.get("sub") or not id_info.get("email"):
            raise InvalidIdTokenError("Google id_token is missing identity claims")

        if expected_nonce_hash is not None:
            nonce = id_info.get("nonce")
            if not nonce or hash_oauth_value(nonce) != expected_nonce_hash:
                raise InvalidIdTokenError("Invalid Google nonce")

        email_verified = id_info.get("email_verified") is True
        if not email_verified:
            raise EmailNotVerifiedError("Google email is not verified")

        return id_info

    def normalize_profile(self, id_info: dict) -> GoogleUserProfile:
        return GoogleUserProfile(
            provider="google",
            provider_user_id=str(id_info["sub"]),
            email=str(id_info["email"]).strip().lower(),
            email_verified=id_info.get("email_verified") is True,
            full_name=id_info.get("name"),
            first_name=id_info.get("given_name"),
            last_name=id_info.get("family_name"),
            avatar_url=id_info.get("picture"),
        )
