"""Configuration safety.

These assert the failure mode, not the happy path: production must refuse to
start rather than run with a development default. Each of these misconfigurations
is silent when wrong, which is exactly why it needs a test.
"""

from __future__ import annotations

import pytest

from app.config import DEV_DATABASE_URL, DEV_JWT_SECRET, InsecureConfigurationError, Settings

GOOD_SECRET = "x" * 48
GOOD_DB = "postgresql+psycopg://user:pw@db.example.com/safeshield"

# Settings reads the process environment as well as .env, and both CI and a
# local shell export these. Without clearing them these tests assert against
# whatever the developer happens to have exported, which is why the
# "defaults are allowed" case passed alone and failed in the full run.
_SETTINGS_ENV_VARS = (
    "ENV",
    "DEBUG",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "JWT_SECRET",
    "COOKIE_SECURE",
    "COOKIE_SAMESITE",
    "CORS_ORIGINS",
    "REDIS_URL",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert against the declared defaults, not the ambient environment."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _prod(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "ENV": "prod",
        "JWT_SECRET": GOOD_SECRET,
        "DATABASE_URL": GOOD_DB,
        "COOKIE_SECURE": True,
        "DEBUG": False,
        "CORS_ORIGINS": ["https://safeshield.example"],
        "_env_file": None,  # ignore any local .env so the test is deterministic
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


class TestProductionRejectsInsecureDefaults:
    def test_a_fully_configured_production_env_is_accepted(self):
        settings = _prod()
        assert settings.ENV == "prod"

    def test_rejects_the_development_signing_key(self):
        """A shared signing key means anyone can mint a valid token."""
        with pytest.raises(InsecureConfigurationError, match="JWT_SECRET"):
            _prod(JWT_SECRET=DEV_JWT_SECRET)

    def test_rejects_a_short_signing_key(self):
        with pytest.raises(InsecureConfigurationError, match="32 characters"):
            _prod(JWT_SECRET="tooshort")

    def test_rejects_the_development_database(self):
        with pytest.raises(InsecureConfigurationError, match="DATABASE_URL"):
            _prod(DATABASE_URL=DEV_DATABASE_URL)

    def test_rejects_insecure_cookies(self):
        """Without Secure, the refresh token travels over plain HTTP."""
        with pytest.raises(InsecureConfigurationError, match="COOKIE_SECURE"):
            _prod(COOKIE_SECURE=False)

    def test_rejects_debug_mode(self):
        with pytest.raises(InsecureConfigurationError, match="DEBUG"):
            _prod(DEBUG=True)

    def test_rejects_wildcard_cors(self):
        """Credentials are allowed, so a wildcard origin lets any site call the
        API as the logged-in user."""
        with pytest.raises(InsecureConfigurationError, match="CORS_ORIGINS"):
            _prod(CORS_ORIGINS=["*"])

    def test_rejects_plaintext_cors_origins(self):
        with pytest.raises(InsecureConfigurationError, match="https"):
            _prod(CORS_ORIGINS=["http://safeshield.example"])

    def test_reports_every_problem_at_once(self):
        """One boot, one complete list -- not a game of whack-a-mole."""
        with pytest.raises(InsecureConfigurationError) as exc:
            _prod(JWT_SECRET=DEV_JWT_SECRET, COOKIE_SECURE=False, DEBUG=True)
        message = str(exc.value)
        assert "JWT_SECRET" in message
        assert "COOKIE_SECURE" in message
        assert "DEBUG" in message


class TestDevelopmentStaysConvenient:
    def test_defaults_are_allowed_outside_production(self):
        """The point is that the project runs locally with no setup."""
        settings = Settings(ENV="dev", _env_file=None)  # type: ignore[call-arg]
        assert settings.JWT_SECRET == DEV_JWT_SECRET

    def test_generate_secret_is_long_enough_for_production(self):
        assert len(Settings.generate_secret()) >= 32


class TestNoSecretsInDefaults:
    def test_no_default_looks_like_a_real_credential(self):
        """Guards against someone pasting a live key in as a default."""
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert not settings.OPENAI_API_KEY, "OPENAI_API_KEY must default to empty"
        for value in (settings.JWT_SECRET, str(settings.DATABASE_URL)):
            assert not value.startswith("sk-"), "an API key leaked into a default"
