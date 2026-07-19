"""Demo sign-in and rate limiting."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User


class TestDemoLogin:
    async def test_creates_the_demo_account_on_first_use(
        self, client: AsyncClient, db: AsyncSession
    ):
        resp = await client.post("/api/auth/demo")
        assert resp.status_code == 200, resp.text
        assert resp.json()["access_token"]

        user = await db.scalar(select(User).where(User.email == settings.DEMO_EMAIL))
        assert user is not None
        assert user.is_demo is True

    async def test_is_reusable_without_creating_duplicates(
        self, client: AsyncClient, db: AsyncSession
    ):
        """A reviewer may click it repeatedly; it must not accumulate accounts."""
        for _ in range(3):
            assert (await client.post("/api/auth/demo")).status_code == 200

        count = await db.scalar(
            select(func.count()).select_from(User).where(User.email == settings.DEMO_EMAIL)
        )
        assert count == 1

    async def test_token_works_on_protected_routes(self, client: AsyncClient):
        token = (await client.post("/api/auth/demo")).json()["access_token"]
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["is_demo"] is True

    async def test_demo_password_is_unusable_for_normal_login(
        self, client: AsyncClient, db: AsyncSession
    ):
        """The account exists but has a random unusable password, so /demo is
        the only way in -- nobody can guess their way into it."""
        await client.post("/api/auth/demo")
        for guess in ["demo", "password", "", settings.DEMO_EMAIL]:
            resp = await client.post(
                "/api/auth/login", json={"email": settings.DEMO_EMAIL, "password": guess or "x"}
            )
            assert resp.status_code in {401, 422}

    async def test_demo_uploads_stay_scoped_to_the_demo_user(self, client: AsyncClient):
        """Demo is a normal account for isolation purposes -- it sees the shared
        corpus, not other users' documents."""
        token = (await client.post("/api/auth/demo")).json()["access_token"]
        listed = (
            await client.get("/api/documents", headers={"Authorization": f"Bearer {token}"})
        ).json()["documents"]
        assert all(d["is_shared"] for d in listed)


class TestRateLimiting:
    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        """Limiter state is process-global; clear it so tests do not bleed."""
        from app.core.ratelimit import limiter

        limiter.reset()
        yield
        limiter.reset()

    async def test_login_is_limited(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        """Guards the LLM budget and slows credential stuffing. Limit is set
        very low here; the real value is generous enough that a human never
        hits it."""
        from app.core.ratelimit import limiter

        if not limiter.enabled:
            pytest.skip("rate limiting disabled")

        statuses = [
            (
                await client.post(
                    "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-pw"}
                )
            ).status_code
            for _ in range(40)
        ]
        assert 429 in statuses, "no request was rate limited"
        # Everything before the limit should be a normal auth rejection.
        assert statuses[0] == 401

    async def test_limit_response_is_a_429_not_a_500(self, client: AsyncClient):
        from app.core.ratelimit import limiter

        if not limiter.enabled:
            pytest.skip("rate limiting disabled")

        last = 200
        for _ in range(40):
            last = (
                await client.post(
                    "/api/auth/login", json={"email": "x@example.com", "password": "wrong-pw"}
                )
            ).status_code
            if last == 429:
                break
        assert last == 429
