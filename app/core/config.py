from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Labora Backend"
    app_env: str = "development"
    app_debug: bool = True
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./labora.db"
    jwt_secret_key: str = "change_me_in_local_dev"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    cors_origins: list[str] = ["http://localhost:3000"]
    local_storage_path: str = "./storage"
    max_upload_size_mb: int = 25

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
