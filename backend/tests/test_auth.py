"""Auth flow tests."""

import pytest
from httpx import AsyncClient

from app.api.auth import REFRESH_COOKIE

CREDS = {"email": "asha@example.com", "password": "correct-horse-battery"}


async def _register(client: AsyncClient, **overrides: str) -> dict[str, str]:
    payload = {**CREDS, "full_name": "Asha Rao", **overrides}
    resp = await client.post("/api/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    body: dict[str, str] = resp.json()
    return body


class TestRegister:
    async def test_returns_access_token_and_sets_refresh_cookie(self, client: AsyncClient):
        resp = await client.post("/api/auth/register", json=CREDS)
        assert resp.status_code == 201
        body = resp.json()
        assert body["access_token"]
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        assert REFRESH_COOKIE in resp.cookies
        # The refresh token must never appear in the response body.
        assert "refresh" not in resp.text.lower()

    async def test_duplicate_email_conflicts(self, client: AsyncClient):
        await _register(client)
        resp = await client.post("/api/auth/register", json=CREDS)
        assert resp.status_code == 409

    async def test_email_is_case_insensitive(self, client: AsyncClient):
        await _register(client)
        resp = await client.post("/api/auth/register", json={**CREDS, "email": "ASHA@example.com"})
        assert resp.status_code == 409

    @pytest.mark.parametrize("password", ["short", ""])
    async def test_rejects_weak_password(self, client: AsyncClient, password: str):
        resp = await client.post("/api/auth/register", json={**CREDS, "password": password})
        assert resp.status_code == 422

    async def test_rejects_malformed_email(self, client: AsyncClient):
        resp = await client.post("/api/auth/register", json={**CREDS, "email": "not-an-email"})
        assert resp.status_code == 422


class TestLogin:
    async def test_succeeds_with_correct_password(self, client: AsyncClient):
        await _register(client)
        resp = await client.post("/api/auth/login", json=CREDS)
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    async def test_rejects_wrong_password(self, client: AsyncClient):
        await _register(client)
        resp = await client.post("/api/auth/login", json={**CREDS, "password": "wrong-password"})
        assert resp.status_code == 401

    async def test_rejects_unknown_email(self, client: AsyncClient):
        resp = await client.post("/api/auth/login", json=CREDS)
        assert resp.status_code == 401

    async def test_error_message_does_not_leak_email_existence(self, client: AsyncClient):
        await _register(client)
        wrong_pw = await client.post("/api/auth/login", json={**CREDS, "password": "nope-nope"})
        unknown = await client.post(
            "/api/auth/login", json={"email": "ghost@example.com", "password": "nope-nope"}
        )
        assert wrong_pw.json()["detail"] == unknown.json()["detail"]


class TestMe:
    async def test_returns_current_user(self, client: AsyncClient):
        token = (await _register(client))["access_token"]
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == CREDS["email"]
        assert body["full_name"] == "Asha Rao"
        assert "password_hash" not in body

    async def test_requires_a_token(self, client: AsyncClient):
        assert (await client.get("/api/auth/me")).status_code == 401

    async def test_rejects_garbage_token(self, client: AsyncClient):
        resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"})
        assert resp.status_code == 401


class TestRefresh:
    async def test_rotates_and_issues_new_tokens(self, client: AsyncClient):
        await _register(client)
        resp = await client.post("/api/auth/refresh")
        assert resp.status_code == 200
        assert resp.json()["access_token"]
        assert REFRESH_COOKIE in resp.cookies

    async def test_new_access_token_works(self, client: AsyncClient):
        await _register(client)
        token = (await client.post("/api/auth/refresh")).json()["access_token"]
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    async def test_requires_a_cookie(self, client: AsyncClient):
        assert (await client.post("/api/auth/refresh")).status_code == 401

    async def test_reuse_of_rotated_token_revokes_the_family(self, client: AsyncClient):
        """The stolen-token scenario: replaying a spent token must not merely be
        rejected, it must revoke every sibling so the attacker's copy dies too.

        Each request sets the cookie explicitly rather than relying on the jar:
        the 401 responses clear it, so a jar-driven follow-up would 401 with
        "missing refresh token" and pass for entirely the wrong reason.
        """
        await _register(client)
        stolen = client.cookies[REFRESH_COOKIE]

        rotated = await client.post("/api/auth/refresh")
        assert rotated.status_code == 200
        current = rotated.cookies[REFRESH_COOKIE]
        assert current != stolen

        # Attacker replays the spent token.
        client.cookies.set(REFRESH_COOKIE, stolen)
        replay = await client.post("/api/auth/refresh")
        assert replay.status_code == 401
        assert "reuse" in replay.json()["detail"].lower()

        # The legitimate, still-current token must now be dead as well -- and
        # specifically because it was revoked, not because it went missing.
        client.cookies.set(REFRESH_COOKIE, current)
        after = await client.post("/api/auth/refresh")
        assert after.status_code == 401
        assert "missing" not in after.json()["detail"].lower()


class TestLogout:
    async def test_clears_cookie_and_kills_the_session(self, client: AsyncClient):
        await _register(client)
        assert (await client.post("/api/auth/logout")).status_code == 204
        assert (await client.post("/api/auth/refresh")).status_code == 401

    async def test_is_idempotent_without_a_cookie(self, client: AsyncClient):
        assert (await client.post("/api/auth/logout")).status_code == 204


class TestHealth:
    async def test_health_is_open(self, client: AsyncClient):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
