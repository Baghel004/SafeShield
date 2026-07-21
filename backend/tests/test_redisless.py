"""Single-process mode: running with no Redis and no separate worker.

This is the free-tier deployment shape -- one web instance, ingestion folded
into the API process, rate limiting kept in memory. These assert the app is
actually healthy in that mode, since a readiness check that failed on a Redis
it does not use would take the whole service down on the host.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.api import documents
from app.config import settings
from app.core.ratelimit import _storage_uri


@pytest.fixture
def _no_redis(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)


class TestReadinessWithoutRedis:
    async def test_ready_checks_only_the_database(self, client: AsyncClient, _no_redis):
        """With Redis disabled the probe must not include it, and must not fail
        for its absence -- otherwise the host marks a healthy service down."""
        resp = await client.get("/api/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["checks"] == {"database": "ok"}
        assert "redis" not in body["checks"]

    async def test_redis_is_still_checked_when_enabled(self, client: AsyncClient):
        """The default (full) deployment keeps probing Redis."""
        resp = await client.get("/api/ready")
        assert resp.status_code == 200
        assert "redis" in resp.json()["checks"]


class TestRateLimitStorage:
    def test_uses_in_memory_storage_when_redis_disabled(self, _no_redis):
        assert _storage_uri() is None

    def test_uses_redis_when_enabled(self):
        assert _storage_uri() == settings.REDIS_URL


class TestInProcessIngestion:
    async def test_upload_ingests_in_process_without_redis(self, client: AsyncClient, _no_redis):
        """Without Redis, an upload must not try to reach a queue; it schedules
        ingestion in the API process instead."""
        scheduled: list[uuid.UUID] = []
        monkeyed = documents._ingest_in_process

        def _spy(document_id: uuid.UUID) -> None:
            scheduled.append(document_id)

        with patch.object(documents, "_ingest_in_process", _spy):
            reg = await client.post(
                "/api/auth/register",
                json={"email": "inproc@example.com", "password": "correct-horse-battery"},
            )
            headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
            resp = await client.post(
                "/api/documents",
                files={
                    "file": ("p.pdf", b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF", "application/pdf")
                },
                headers=headers,
            )

        assert resp.status_code == 202
        assert len(scheduled) == 1, "ingestion was not scheduled in process"
        # The spy replaced the real one only inside the with-block.
        assert documents._ingest_in_process is monkeyed

    async def test_upload_uses_the_queue_when_redis_enabled(self, client: AsyncClient):
        """The default path still enqueues to ARQ rather than ingesting inline."""
        with patch.object(documents, "_ingest_in_process") as inproc:
            reg = await client.post(
                "/api/auth/register",
                json={"email": "queued@example.com", "password": "correct-horse-battery"},
            )
            headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
            # The real ARQ enqueue runs against the compose Redis in CI; we only
            # assert the inline path was not taken.
            await client.post(
                "/api/documents",
                files={
                    "file": ("p.pdf", b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF", "application/pdf")
                },
                headers=headers,
            )
        inproc.assert_not_called()
