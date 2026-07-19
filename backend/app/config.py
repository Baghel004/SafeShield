"""Application settings.

Every secret is supplied by the environment; nothing sensitive is committed.
The defaults below exist only so the project runs locally out of the box, and
`ENV=prod` rejects each one of them at startup -- see `_reject_insecure_defaults`.
Failing to boot is the correct response to a missing secret: the alternative is
running in production with a publicly-known signing key.
"""

import secrets
from functools import lru_cache
from typing import Literal, Self

from pydantic import PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinels for values that are safe locally and unacceptable in production.
DEV_JWT_SECRET = "dev-only-insecure-secret-change-me"  # noqa: S105
DEV_DATABASE_URL = "postgresql+psycopg://safeshield:safeshield@localhost:5432/safeshield"


class InsecureConfigurationError(RuntimeError):
    """Raised when production is asked to run with a development default."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: Literal["dev", "test", "prod"] = "dev"
    DEBUG: bool = False

    # --- Database ---
    # psycopg3 serves both the async app engine and sync Alembic from this one URL.
    # Typed as PostgresDsn so a malformed URL fails at startup, not on first query.
    DATABASE_URL: PostgresDsn = PostgresDsn(DEV_DATABASE_URL)

    # --- Auth ---
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    JWT_SECRET: str = DEV_JWT_SECRET
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

    # --- Retrieval ---
    # Candidates pulled from each retriever before fusion. Larger costs little
    # (both are index scans) and gives RRF more to work with.
    RETRIEVAL_CANDIDATES: int = 30
    # Chunks handed to the model. Enough for a grounded answer, few enough that
    # the prompt stays cheap and the model does not lose the question in context.
    RETRIEVAL_TOP_K: int = 6
    # RRF damping. 60 is the value from the original paper and is not sensitive;
    # it mainly stops rank 1 from dominating rank 2.
    RRF_K: int = 60

    # --- Answer generation ---
    # Below this fused score the corpus almost certainly does not contain the
    # answer, and the model is told to say so rather than reach.
    MIN_RETRIEVAL_SCORE: float = 0.01
    ANSWER_MAX_TOKENS: int = 800

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

    @model_validator(mode="after")
    def _reject_insecure_defaults(self) -> Self:
        """Refuse to start in production with a development default.

        Each of these is silent when wrong: a shared signing key means anyone
        can mint a valid token, a non-Secure cookie is sent over plain HTTP, and
        a wildcard CORS origin with credentials enabled lets any site call the
        API as the logged-in user. A crash at boot is far cheaper than any of
        them being discovered later.
        """
        if self.ENV != "prod":
            return self

        problems: list[str] = []
        if self.JWT_SECRET == DEV_JWT_SECRET:
            problems.append("JWT_SECRET is the development default")
        if len(self.JWT_SECRET) < 32:
            problems.append("JWT_SECRET is shorter than 32 characters")
        if str(self.DATABASE_URL) == DEV_DATABASE_URL:
            problems.append("DATABASE_URL is the development default")
        if not self.COOKIE_SECURE:
            problems.append("COOKIE_SECURE must be true in production (HTTPS only)")
        if self.DEBUG:
            problems.append("DEBUG must be false in production")
        if "*" in self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS must not be '*' when credentials are allowed")
        if any(o.startswith("http://") for o in self.CORS_ORIGINS):
            problems.append("CORS_ORIGINS must use https in production")

        if problems:
            raise InsecureConfigurationError(
                "Refusing to start with ENV=prod:\n  - "
                + "\n  - ".join(problems)
                + "\n\nSet these in the environment. Generate a secret with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

    @staticmethod
    def generate_secret() -> str:
        """Convenience for operators provisioning a deployment."""
        return secrets.token_urlsafe(48)

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations synchronously. psycopg3 handles sync and async
        under the same URL scheme, so there is nothing to swap."""
        return str(self.DATABASE_URL)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
