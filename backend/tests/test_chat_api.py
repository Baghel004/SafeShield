"""Chat endpoint: auth, scoping, SSE shape.

The model is never called: these cover the request surface and the ownership
boundary, and `stream_answer` is patched so the suite needs no API key, costs
nothing, and does not depend on what a model happens to say.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus


async def _auth(client: AsyncClient, email: str = "chat@example.com") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": "correct-horse-battery"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch: pytest.MonkeyPatch):
    """Replace generation with a fixed reply, so no API key or spend is needed."""

    async def fake_stream(question: str, chunks: list[object]) -> AsyncIterator[str]:  # noqa: ARG001
        for piece in ("The waiting period ", "is 9 months [1]."):
            yield piece

    monkeypatch.setattr("app.api.chat.stream_answer", fake_stream)


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        name = payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        if name:
            events.append((name, payload or {}))
    return events


class TestAuth:
    async def test_streaming_requires_authentication(self, client: AsyncClient):
        resp = await client.post("/api/chat", json={"question": "Is maternity covered?"})
        assert resp.status_code == 401

    async def test_sync_requires_authentication(self, client: AsyncClient):
        resp = await client.post("/api/chat/sync", json={"question": "Is maternity covered?"})
        assert resp.status_code == 401


class TestValidation:
    @pytest.mark.parametrize("question", ["", "  ", "hi"])
    async def test_rejects_too_short_questions(self, client: AsyncClient, question: str):
        headers = await _auth(client)
        resp = await client.post("/api/chat/sync", json={"question": question}, headers=headers)
        assert resp.status_code == 422

    async def test_rejects_an_overlong_question(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post("/api/chat/sync", json={"question": "x" * 5000}, headers=headers)
        assert resp.status_code == 422


class TestSyncAnswer:
    async def test_returns_answer_and_grounded_flag(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat/sync", json={"question": "Is maternity covered?"}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "answer" in body
        assert isinstance(body["citations"], list)
        assert isinstance(body["grounded"], bool)


class TestStreaming:
    async def test_streams_server_sent_events(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat", json={"question": "Is maternity covered?"}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        names = [name for name, _ in _parse_sse(resp.text)]
        assert names[0] == "citations", "sources must arrive first so the UI can render them"
        assert "token" in names
        assert names[-1] == "done"

    async def test_tokens_reassemble_into_the_answer(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat", json={"question": "Is maternity covered?"}, headers=headers
        )
        text = "".join(d["text"] for n, d in _parse_sse(resp.text) if n == "token")
        assert text == "The waiting period is 9 months [1]."

    async def test_buffering_is_disabled_for_proxies(self, client: AsyncClient):
        """Without this nginx buffers the whole response and streaming is lost."""
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat", json={"question": "Is maternity covered?"}, headers=headers
        )
        assert resp.headers.get("x-accel-buffering") == "no"
        assert resp.headers.get("cache-control") == "no-cache"


class TestUpstreamFailure:
    """What the caller sees when the model call fails.

    The two endpoints must fail differently, and both deliberately. Streaming
    has already committed to a 200 and flushed the citations event by the time
    generation starts, so it reports in-band; sync has sent nothing yet, so it
    can still use a status code and should.
    """

    @pytest.fixture
    def _broken_model(self, monkeypatch: pytest.MonkeyPatch):
        async def boom(question: str, chunks: list[object]) -> AsyncIterator[str]:  # noqa: ARG001
            raise RuntimeError("upstream is down")
            yield  # pragma: no cover -- makes this an async generator

        monkeypatch.setattr("app.api.chat.stream_answer", boom)

    @pytest.mark.usefixtures("_broken_model")
    async def test_sync_returns_502_not_an_unhandled_500(self, client: AsyncClient):
        """Previously this propagated as a bare 500 with a traceback. 502 is the
        accurate status: the upstream model failed, not the request."""
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat/sync", json={"question": "Is maternity covered?"}, headers=headers
        )
        assert resp.status_code == 502
        assert "upstream is down" not in resp.text, "internal detail must not leak to the caller"

    @pytest.mark.usefixtures("_broken_model")
    async def test_streaming_reports_the_failure_in_band(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat", json={"question": "Is maternity covered?"}, headers=headers
        )
        # Still 200: the status line was sent before generation began.
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        names = [name for name, _ in events]
        assert "error" in names, "a failed stream must say so rather than just stopping"
        assert "done" not in names, "a failed stream must not claim completion"


class TestDocumentScoping:
    async def test_unknown_document_is_404(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post(
            "/api/chat/sync",
            json={"question": "Is maternity covered?", "document_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert resp.status_code == 404

    async def test_cannot_scope_to_another_users_document(
        self, client: AsyncClient, db: AsyncSession
    ):
        """404 rather than 403 -- a 403 confirms the document exists."""
        alice = await _auth(client, "alice-chat@example.com")
        upload = await client.post(
            "/api/documents",
            files={
                "file": ("a.pdf", b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF", "application/pdf")
            },
            headers=alice,
        )
        doc_id = upload.json()["id"]

        bob = await _auth(client, "bob-chat@example.com")
        resp = await client.post(
            "/api/chat/sync",
            json={"question": "Is maternity covered?", "document_id": doc_id},
            headers=bob,
        )
        assert resp.status_code == 404

    async def test_shared_documents_are_addressable(self, client: AsyncClient, db: AsyncSession):
        shared = Document(
            user_id=None, filename="shared.pdf", status=DocumentStatus.READY, size_bytes=1
        )
        db.add(shared)
        await db.flush()

        headers = await _auth(client, "reader@example.com")
        resp = await client.post(
            "/api/chat/sync",
            json={"question": "Is maternity covered?", "document_id": str(shared.id)},
            headers=headers,
        )
        assert resp.status_code == 200
