from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Labora Backend"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True

    API_V1_PREFIX: str = "/api/v1"

    JWT_SECRET_KEY: str = "change-this-secret-before-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    DATABASE_URL: str

    CORS_ORIGINS: str = "http://localhost:3000"

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
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

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
