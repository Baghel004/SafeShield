"""Limits, recovery and failure handling.

Each of these covers something that was silent when wrong: a cap that was
declared but never enforced, a rate-limit key that was read but never written,
and a queue failure the upload path deliberately swallows. None of them would
surface as a crash, so none of them would have been noticed without a test that
asserts the behaviour directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.files import InvalidUploadError, validate_pdf_bytes
from app.core.ratelimit import client_key
from app.models.document import Document, DocumentStatus
from app.models.user import User

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
REAL_PDF = DATASETS / "BAJHLIP23020V012223.pdf"  # 49 pages

needs_datasets = pytest.mark.skipif(not REAL_PDF.exists(), reason="sample PDFs not present")


# --------------------------------------------------------------------------
# Page cap
# --------------------------------------------------------------------------


@needs_datasets
class TestPageCap:
    """MAX_PDF_PAGES was declared in config and documented in .env.example, but
    nothing read it. Size is a poor proxy: PDFs of text compress well, so a
    thousand-page document sits under a 25MB cap while costing far more to
    embed than a short scanned one."""

    def test_accepts_a_document_within_the_cap(self):
        validate_pdf_bytes(REAL_PDF.read_bytes(), settings.MAX_UPLOAD_BYTES, max_pages=400)

    def test_rejects_a_document_over_the_cap(self):
        with pytest.raises(InvalidUploadError, match="pages"):
            validate_pdf_bytes(REAL_PDF.read_bytes(), settings.MAX_UPLOAD_BYTES, max_pages=10)

    def test_reports_the_actual_page_count(self):
        """The message has to say what was wrong, not just that something was."""
        with pytest.raises(InvalidUploadError, match="49 pages"):
            validate_pdf_bytes(REAL_PDF.read_bytes(), settings.MAX_UPLOAD_BYTES, max_pages=10)

    def test_rejects_a_file_that_cannot_be_parsed(self):
        """Starts with %PDF- and passes the magic-byte check, but is not a PDF.

        Rejected at upload rather than accepted and failed asynchronously, where
        the user would have to poll to discover it.
        """
        with pytest.raises(InvalidUploadError, match="could not be read"):
            validate_pdf_bytes(b"%PDF-1.4\nthis is not actually a pdf", 1_000_000, max_pages=400)

    def test_page_count_is_skipped_when_no_cap_is_given(self):
        """Parsing every upload twice would be wasteful where no cap applies."""
        validate_pdf_bytes(b"%PDF-1.4\nnot parseable", 1_000_000, max_pages=None)

    async def test_upload_endpoint_enforces_the_cap(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "MAX_PDF_PAGES", 5)
        reg = await client.post(
            "/api/auth/register",
            json={"email": "pagecap@example.com", "password": "correct-horse-battery"},
        )
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        resp = await client.post(
            "/api/documents",
            files={"file": (REAL_PDF.name, REAL_PDF.read_bytes(), "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 422
        assert "pages" in resp.text


# --------------------------------------------------------------------------
# Rate-limit identity
# --------------------------------------------------------------------------


class TestRateLimitKey:
    """client_key preferred the authenticated user over the IP, but nothing ever
    set request.state.user_id -- so every authenticated caller silently shared
    one IP bucket, which is precisely what keying by user exists to prevent."""

    def _request(self, **state: object):
        from starlette.requests import Request

        req = Request({"type": "http", "headers": [], "client": ("203.0.113.7", 1234)})
        for key, value in state.items():
            setattr(req.state, key, value)
        return req

    def test_falls_back_to_the_address_when_anonymous(self):
        assert client_key(self._request()) == "ip:203.0.113.7"

    def test_prefers_the_authenticated_user(self):
        user_id = str(uuid.uuid4())
        assert client_key(self._request(user_id=user_id)) == f"user:{user_id}"

    def test_two_users_do_not_share_a_bucket(self):
        """The property that matters: an office behind one NAT is many clients."""
        a = client_key(self._request(user_id=str(uuid.uuid4())))
        b = client_key(self._request(user_id=str(uuid.uuid4())))
        assert a != b

    async def test_authentication_populates_the_key(self, client: AsyncClient, db: AsyncSession):
        """The dependency is what closes the loop: client_key reads
        request.state.user_id, and get_current_user is the only thing that
        writes it. Asserted against the real dependency rather than through an
        endpoint, because slowapi captures key_func when the route is decorated,
        so the limiter cannot be instrumented after import.
        """
        from fastapi.security import HTTPAuthorizationCredentials
        from starlette.requests import Request

        from app.core.deps import get_current_user

        reg = await client.post(
            "/api/auth/register",
            json={"email": "ratekey@example.com", "password": "correct-horse-battery"},
        )
        token = reg.json()["access_token"]

        request = Request({"type": "http", "headers": [], "client": ("203.0.113.7", 1234)})
        assert client_key(request) == "ip:203.0.113.7", "precondition: anonymous keys by address"

        user = await get_current_user(
            request,
            db,
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        )

        assert client_key(request) == f"user:{user.id}"


# --------------------------------------------------------------------------
# Stranded ingestion recovery
# --------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, str]] = []

    async def enqueue_job(self, name: str, *args: str) -> None:
        self.jobs.append((name, args[0]))


@pytest.mark.usefixtures("engine")
class TestStrandedRecovery:
    """The upload endpoint swallows a queue failure on purpose -- the bytes are
    on disk and the row is saved, so failing the request would be the worse
    outcome. That only holds if something re-drives the work, and nothing did:
    a Redis blip during upload stranded the document in `pending` forever while
    the API reported it as still processing."""

    @pytest.fixture
    async def factory(self, engine, monkeypatch: pytest.MonkeyPatch):
        """Point the task at the test database.

        The task opens its own session from the module-level SessionLocal --
        correctly, since it runs in the worker with no request scope to inherit
        one from. That factory is bound to the application database, so without
        this the task queries a different database than the one the test wrote
        to and reports nothing stranded.
        """
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr("app.worker.tasks.SessionLocal", maker)
        return maker

    async def _make(self, factory, *, age_seconds: int, status: DocumentStatus) -> uuid.UUID:
        async with factory() as db:
            user = User(email=f"stranded-{uuid.uuid4()}@example.com", password_hash="x")
            db.add(user)
            await db.flush()
            doc = Document(
                user_id=user.id,
                filename="stranded.pdf",
                status=status,
                size_bytes=1,
                created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            )
            db.add(doc)
            await db.commit()
            return doc.id

    async def _cleanup(self, factory, doc_ids: list[uuid.UUID]) -> None:
        async with factory() as db:
            for doc_id in doc_ids:
                doc = await db.get(Document, doc_id)
                if doc:
                    user = await db.get(User, doc.user_id)
                    await db.delete(doc)
                    if user:
                        await db.delete(user)
            await db.commit()

    async def test_requeues_a_document_stuck_in_pending(self, factory):
        from app.worker.tasks import requeue_stranded_documents

        stale = await self._make(
            factory,
            age_seconds=settings.INGEST_STRANDED_AFTER_SECONDS + 60,
            status=DocumentStatus.PENDING,
        )
        redis = _FakeRedis()
        try:
            count = await requeue_stranded_documents({"redis": redis})
            assert count >= 1
            assert ("ingest_document_task", str(stale)) in redis.jobs
        finally:
            await self._cleanup(factory, [stale])

    async def test_leaves_a_recent_upload_alone(self, factory):
        """A document enqueued seconds ago is legitimately pending. Re-driving it
        would run ingestion twice and bill the embedding calls twice."""
        from app.worker.tasks import requeue_stranded_documents

        fresh = await self._make(factory, age_seconds=5, status=DocumentStatus.PENDING)
        redis = _FakeRedis()
        try:
            await requeue_stranded_documents({"redis": redis})
            assert str(fresh) not in [doc_id for _, doc_id in redis.jobs]
        finally:
            await self._cleanup(factory, [fresh])

    @pytest.mark.parametrize("status", [DocumentStatus.READY, DocumentStatus.FAILED])
    async def test_ignores_documents_that_are_not_pending(self, factory, status):
        from app.worker.tasks import requeue_stranded_documents

        done = await self._make(
            factory, age_seconds=settings.INGEST_STRANDED_AFTER_SECONDS + 60, status=status
        )
        redis = _FakeRedis()
        try:
            await requeue_stranded_documents({"redis": redis})
            assert str(done) not in [doc_id for _, doc_id in redis.jobs]
        finally:
            await self._cleanup(factory, [done])

    async def test_reports_nothing_to_do_when_the_queue_is_healthy(self, factory):
        from app.worker.tasks import requeue_stranded_documents

        async with factory() as db:
            pending = select(Document.id).where(Document.status == DocumentStatus.PENDING)
            leftover = (await db.scalars(pending)).all()
        if leftover:
            pytest.skip("another test left pending documents behind")

        assert await requeue_stranded_documents({"redis": _FakeRedis()}) == 0
