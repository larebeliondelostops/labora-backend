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
    JWT_ACCESS_SECRET: str = ""
    JWT_ACCESS_TTL_SECONDS: int = 900
    REFRESH_TOKEN_TTL_DAYS: int = 30
    PASSWORD_HASH_ALGORITHM: str = "bcrypt"
    OTP_TTL_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 900
    AUTH_RATE_LIMIT_MAX_ATTEMPTS: int = 10
    APP_FRONTEND_URL: str = ""
    EMAIL_PROVIDER: str = "console"
    EMAIL_FROM: str = "no-reply@labora.local"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False

    DATABASE_URL: str

    CORS_ORIGINS: str = "http://localhost:3000,https://labora.centralspike.com"
    CORS_ALLOW_CREDENTIALS: bool = True

    FRONTEND_URL: str = "http://localhost:3000"
    BACKEND_PUBLIC_URL: str = "http://localhost:8000"
    API_PUBLIC_BASE_URL: str = ""
    PUBLIC_API_URL: str = ""

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
    STORAGE_BACKEND: str = "local"
    MINIO_ENDPOINT: str = "labora-minio:9000"
    MINIO_PUBLIC_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "labora_minio"
    MINIO_SECRET_KEY: str = "labora_minio_password"
    MINIO_BUCKET: str = "documents"
    MINIO_REGION: str = "us-east-1"
    MINIO_SECURE: bool = False
    MINIO_PRESIGNED_UPLOAD_TTL_SECONDS: int = 900

    AI_PROVIDER: str = "mock"
    AI_API_KEY: str = ""
    AI_BASE_URL: str = ""
    AI_MODEL: str = ""
    AI_TIMEOUT_MS: int = 30000
    AI_MAX_RETRIES: int = 2
    AI_TEMPERATURE: float = 0
    AI_JSON_MODE: bool = True

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
    def jwt_access_secret(self) -> str:
        return self.JWT_ACCESS_SECRET or self.JWT_SECRET_KEY

    @property
    def jwt_algorithm(self) -> str:
        return self.JWT_ALGORITHM

    @property
    def access_token_expire_minutes(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES

    @property
    def jwt_access_ttl_seconds(self) -> int:
        return self.JWT_ACCESS_TTL_SECONDS

    @property
    def refresh_token_ttl_days(self) -> int:
        return self.REFRESH_TOKEN_TTL_DAYS

    @property
    def otp_ttl_minutes(self) -> int:
        return self.OTP_TTL_MINUTES

    @property
    def otp_max_attempts(self) -> int:
        return self.OTP_MAX_ATTEMPTS

    @property
    def auth_rate_limit_window_seconds(self) -> int:
        return self.AUTH_RATE_LIMIT_WINDOW_SECONDS

    @property
    def auth_rate_limit_max_attempts(self) -> int:
        return self.AUTH_RATE_LIMIT_MAX_ATTEMPTS

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
        return (self.APP_FRONTEND_URL or self.FRONTEND_URL).rstrip("/")

    @property
    def backend_public_url(self) -> str:
        return (
            self.API_PUBLIC_BASE_URL
            or self.PUBLIC_API_URL
            or self.BACKEND_PUBLIC_URL
        ).rstrip("/")

    @property
    def email_provider(self) -> str:
        return self.EMAIL_PROVIDER.lower()

    @property
    def email_from(self) -> str:
        return self.EMAIL_FROM

    @property
    def smtp_host(self) -> str:
        return self.SMTP_HOST

    @property
    def smtp_port(self) -> int:
        return self.SMTP_PORT

    @property
    def smtp_username(self) -> str:
        return self.SMTP_USERNAME

    @property
    def smtp_password(self) -> str:
        return self.SMTP_PASSWORD

    @property
    def smtp_use_tls(self) -> bool:
        return self.SMTP_USE_TLS

    @property
    def smtp_use_ssl(self) -> bool:
        return self.SMTP_USE_SSL

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

    @property
    def storage_backend(self) -> str:
        return self.STORAGE_BACKEND.strip().lower()

    @property
    def minio_endpoint(self) -> str:
        return self.MINIO_ENDPOINT

    @property
    def minio_public_endpoint(self) -> str:
        return self.MINIO_PUBLIC_ENDPOINT

    @property
    def minio_access_key(self) -> str:
        return self.MINIO_ACCESS_KEY

    @property
    def minio_secret_key(self) -> str:
        return self.MINIO_SECRET_KEY

    @property
    def minio_bucket(self) -> str:
        return self.MINIO_BUCKET

    @property
    def minio_region(self) -> str:
        return self.MINIO_REGION

    @property
    def minio_secure(self) -> bool:
        return self.MINIO_SECURE

    @property
    def minio_presigned_upload_ttl_seconds(self) -> int:
        return self.MINIO_PRESIGNED_UPLOAD_TTL_SECONDS

    @property
    def ai_provider(self) -> str:
        return self.AI_PROVIDER.strip().lower()

    @property
    def ai_api_key(self) -> str:
        return self.AI_API_KEY

    @property
    def ai_base_url(self) -> str:
        return self.AI_BASE_URL.rstrip("/")

    @property
    def ai_model(self) -> str:
        return self.AI_MODEL

    @property
    def ai_timeout_seconds(self) -> float:
        return max(self.AI_TIMEOUT_MS, 1) / 1000

    @property
    def ai_max_retries(self) -> int:
        return max(self.AI_MAX_RETRIES, 0)

    @property
    def ai_temperature(self) -> float:
        return self.AI_TEMPERATURE

    @property
    def ai_json_mode(self) -> bool:
        return self.AI_JSON_MODE


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
