from functools import lru_cache
import json

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Labora Backend"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True

    API_V1_PREFIX: str = "/api/v1"

    JWT_SECRET_KEY: str = "change-this-secret-before-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120

    DATABASE_URL: str

    CORS_ORIGINS: str = "http://localhost:3000"
    CORS_ALLOW_CREDENTIALS: bool = True

    FRONTEND_URL: str = "http://localhost:3000"

    AUTH_COOKIE_NAME: str = "labora_access_token"
    AUTH_COOKIE_SECURE: bool = False
    AUTH_COOKIE_HTTPONLY: bool = True
    AUTH_COOKIE_SAMESITE: str = "lax"
    AUTH_COOKIE_DOMAIN: str = ""

    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REDIRECT_URI: str = (
        "http://localhost:8000/api/v1/auth/google/callback"
    )
    GOOGLE_OAUTH_SCOPES: str = "openid email profile"

    OAUTH_STATE_EXPIRE_MINUTES: int = 10

    LOCAL_STORAGE_PATH: str = "/app/storage"
    MAX_UPLOAD_SIZE_MB: int = 25

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def app_name(self) -> str:
        return self.APP_NAME

    @property
    def app_env(self) -> str:
        return self.APP_ENV

    @property
    def app_debug(self) -> bool:
        return self.APP_DEBUG

    @property
    def api_v1_prefix(self) -> str:
        return self.API_V1_PREFIX

    @property
    def jwt_secret_key(self) -> str:
        return self.JWT_SECRET_KEY

    @property
    def jwt_algorithm(self) -> str:
        return self.JWT_ALGORITHM

    @property
    def access_token_expire_minutes(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def cors_origins(self) -> list[str]:
        raw_origins = self.CORS_ORIGINS.strip()
        if raw_origins.startswith("["):
            origins = json.loads(raw_origins)
        else:
            origins = raw_origins.split(",")

        return [
            str(origin).strip().rstrip("/")
            for origin in origins
            if str(origin).strip()
        ]

    @property
    def cors_allow_credentials(self) -> bool:
        return self.CORS_ALLOW_CREDENTIALS

    @property
    def frontend_url(self) -> str:
        return self.FRONTEND_URL.rstrip("/")

    @property
    def auth_cookie_name(self) -> str:
        return self.AUTH_COOKIE_NAME

    @property
    def auth_cookie_secure(self) -> bool:
        return self.AUTH_COOKIE_SECURE

    @property
    def auth_cookie_httponly(self) -> bool:
        return self.AUTH_COOKIE_HTTPONLY

    @property
    def auth_cookie_samesite(self) -> str:
        return self.AUTH_COOKIE_SAMESITE

    @property
    def auth_cookie_domain(self) -> str | None:
        return self.AUTH_COOKIE_DOMAIN or None

    @property
    def google_oauth_client_id(self) -> str:
        return self.GOOGLE_OAUTH_CLIENT_ID

    @property
    def google_oauth_client_secret(self) -> str:
        return self.GOOGLE_OAUTH_CLIENT_SECRET

    @property
    def google_oauth_redirect_uri(self) -> str:
        return self.GOOGLE_OAUTH_REDIRECT_URI

    @property
    def google_oauth_scopes(self) -> str:
        return self.GOOGLE_OAUTH_SCOPES

    @property
    def oauth_state_expire_minutes(self) -> int:
        return self.OAUTH_STATE_EXPIRE_MINUTES

    @property
    def local_storage_path(self) -> str:
        return self.LOCAL_STORAGE_PATH

    @property
    def max_upload_size_mb(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
