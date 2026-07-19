"""Application settings, loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: Literal["dev", "test", "prod"] = "dev"
    DEBUG: bool = False

    # --- Database ---
    # psycopg3 serves both the async app engine and sync Alembic from this one URL.
    # Typed as PostgresDsn so a malformed URL fails at startup, not on first query.
    DATABASE_URL: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://safeshield:safeshield@localhost:5432/safeshield"
    )

    # --- Auth ---
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    JWT_SECRET: str = "dev-only-insecure-secret-change-me"  # noqa: S105
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 30

    # Cookie flags. SECURE must be True in production (HTTPS only).
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = None

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Redis / background jobs ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- OpenAI ---
    OPENAI_API_KEY: str | None = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Must match the model above and the vector(N) column in migration 0002.
    # Changing it needs a migration, not just a config edit.
    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_BATCH_SIZE: int = 96
    CHAT_MODEL: str = "gpt-4o-mini"

    # --- Demo access ---
    # Must be a syntactically valid address: EmailStr rejects reserved
    # special-use TLDs like .local, which made /auth/me fail response
    # validation for the demo user. Nothing is ever sent here.
    DEMO_EMAIL: str = "demo@safeshield.app"

    # --- Rate limiting ---
    # Guards the LLM budget: one script in a loop should not be able to run up
    # a bill. Generous enough that a human never notices.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_AUTH: str = "20/minute"
    RATE_LIMIT_UPLOAD: str = "10/hour"
    RATE_LIMIT_CHAT: str = "20/hour"

    # --- Uploads ---
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
    MAX_PDF_PAGES: int = 400
    UPLOAD_DIR: str = "uploads"

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations synchronously. psycopg3 handles sync and async
        under the same URL scheme, so there is nothing to swap."""
        return str(self.DATABASE_URL)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
