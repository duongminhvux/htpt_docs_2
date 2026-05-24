from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    APP_NAME: str = "HTPT Docs"
    ENV: str = "development"

    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/htpt_docs"
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    RABBITMQ_URL: str = "amqp://guest:guest@127.0.0.1:5672/"

    SECRET_KEY: str = "change-this-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    FRONTEND_URL: str = "http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"

    model_config = SettingsConfigDict(
        env_file=[
            BASE_DIR / ".env",
            BACKEND_DIR / ".env",
            ".env",
        ],
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()