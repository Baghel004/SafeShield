"""Ingestion pipeline tests, end to end against pgvector.

Covers the path the background worker actually takes: a PDF on disk becomes
embedded chunks in Postgres, with status transitions and failure handling.
Requires the vector extension, so these skip on a plain Postgres.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.rag.embed import FakeEmbeddings
from app.rag.ingest import IngestionError, ingest_document
from tests.conftest import needs_pgvector

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
SMALL_PDF = DATASETS / "EDLHLGA23009V012223.pdf"  # 2 pages: fast, still real

needs_datasets = pytest.mark.skipif(not SMALL_PDF.exists(), reason="sample PDFs not present")

pytestmark = [needs_pgvector, needs_datasets]


@pytest.fixture
def provider() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
async def factory(engine) -> async_sessionmaker[AsyncSession]:
    """A session factory bound to the test engine.

    ingest_document opens its own sessions -- it must not hold one across the
    embedding call -- so it needs a factory, not a session. That means these
    tests commit for real; each one cleans up the document it created.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def _make_document(
    factory: async_sessionmaker[AsyncSession], *, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    async with factory() as db:
        doc = Document(
            user_id=user_id,
            filename=SMALL_PDF.name,
            title=SMALL_PDF.stem,
            status=DocumentStatus.PENDING,
            size_bytes=SMALL_PDF.stat().st_size,
        )
        db.add(doc)
        await db.commit()
        return doc.id


async def _cleanup(factory: async_sessionmaker[AsyncSession], doc_id: uuid.UUID) -> None:
    async with factory() as db:
        doc = await db.scalar(select(Document).where(Document.id == doc_id))
        if doc is not None:
            await db.delete(doc)
            await db.commit()


@pytest.fixture
async def document(factory):
    doc_id = await _make_document(factory)
    yield doc_id
    await _cleanup(factory, doc_id)


class TestIngestion:
    async def test_stores_chunks_and_marks_ready(self, factory, document, provider):
        count = await ingest_document(factory, document, SMALL_PDF, provider)
        assert count > 0

        async with factory() as db:
            doc = await db.scalar(select(Document).where(Document.id == document))
            stored = await db.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document)
            )

        assert doc.status == DocumentStatus.READY
        assert doc.error is None
        assert doc.chunk_count == count == stored
        assert doc.page_count > 0

    async def test_chunks_carry_embeddings_and_metadata(self, factory, document, provider):
        await ingest_document(factory, document, SMALL_PDF, provider)

        async with factory() as db:
            chunks = (
                await db.scalars(
                    select(Chunk).where(Chunk.document_id == document).order_by(Chunk.ordinal)
                )
            ).all()

        assert chunks
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))
        assert all(len(c.embedding) == provider.dimensions for c in chunks)
        assert all(c.body.strip() for c in chunks)
        assert all(c.page_no >= 1 for c in chunks)
        assert any(c.section_path for c in chunks)

    async def test_tsv_is_populated_by_the_trigger(self, factory, document, provider):
        """The keyword half of hybrid retrieval is maintained in the database."""
        await ingest_document(factory, document, SMALL_PDF, provider)

        async with factory() as db:
            missing = await db.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == document, Chunk.tsv.is_(None))
            )
        assert missing == 0

    async def test_reingestion_replaces_rather_than_duplicates(self, factory, document, provider):
        first = await ingest_document(factory, document, SMALL_PDF, provider)
        second = await ingest_document(factory, document, SMALL_PDF, provider)
        assert first == second

        async with factory() as db:
            stored = await db.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document)
            )
        assert stored == second, "re-ingestion duplicated chunks instead of replacing them"

    async def test_chunks_inherit_owner_for_isolation(self, factory, engine, provider):
        """Chunk.user_id is denormalized from the document so retrieval can
        filter on ownership without a join. If it did not propagate, one user's
        document would be searchable by everyone."""
        async with factory() as db:
            owner = User(email=f"owner-{uuid.uuid4()}@example.com", password_hash="x")
            db.add(owner)
            await db.commit()
            owner_id = owner.id

        doc_id = await _make_document(factory, user_id=owner_id)
        try:
            await ingest_document(factory, doc_id, SMALL_PDF, provider)
            async with factory() as db:
                foreign = await db.scalar(
                    select(func.count())
                    .select_from(Chunk)
                    .where(Chunk.document_id == doc_id, Chunk.user_id != owner_id)
                )
            assert foreign == 0
        finally:
            await _cleanup(factory, doc_id)
            async with factory() as db:
                user = await db.scalar(select(User).where(User.id == owner_id))
                if user:
                    await db.delete(user)
                    await db.commit()


class TestFailureHandling:
    async def test_missing_file_marks_failed_with_reason(self, factory, document, provider):
        with pytest.raises(FileNotFoundError):
            await ingest_document(factory, document, DATASETS / "nope.pdf", provider)

        async with factory() as db:
            doc = await db.scalar(select(Document).where(Document.id == document))
        assert doc.status == DocumentStatus.FAILED
        assert doc.error, "a failed document must say why, not sit blank"

    async def test_unknown_document_raises(self, factory, provider):
        with pytest.raises(IngestionError, match="not found"):
            await ingest_document(factory, uuid.uuid4(), SMALL_PDF, provider)

    async def test_non_pdf_marks_failed_rather_than_hanging(
        self, factory, document, provider, tmp_path: Path
    ):
        junk = tmp_path / "junk.pdf"
        junk.write_bytes(b"not a pdf at all")

        with pytest.raises(Exception):  # noqa: B017 -- pdfplumber's type is not part of the contract
            await ingest_document(factory, document, junk, provider)

        async with factory() as db:
            doc = await db.scalar(select(Document).where(Document.id == document))
        assert doc.status == DocumentStatus.FAILED

    async def test_embedding_failure_marks_failed(self, factory, document):
        class BrokenProvider:
            @property
            def dimensions(self) -> int:
                return 1536

            async def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("embedding provider is down")

        with pytest.raises(RuntimeError, match="down"):
            await ingest_document(factory, document, SMALL_PDF, BrokenProvider())

        async with factory() as db:
            doc = await db.scalar(select(Document).where(Document.id == document))
        assert doc.status == DocumentStatus.FAILED
        assert "down" in doc.error

    async def test_vector_count_mismatch_is_caught(self, factory, document):
        """A provider returning the wrong number of vectors would silently
        misalign every chunk with someone else's embedding."""

        class ShortProvider:
            @property
            def dimensions(self) -> int:
                return 1536

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return await FakeEmbeddings().embed(texts[:-1])  # one short

        with pytest.raises(IngestionError, match="mismatch"):
            await ingest_document(factory, document, SMALL_PDF, ShortProvider())

        async with factory() as db:
            doc = await db.scalar(select(Document).where(Document.id == document))
        assert doc.status == DocumentStatus.FAILED


@needs_pgvector
class TestVectorSearch:
    async def test_nearest_neighbour_is_the_chunk_itself(self, factory, document, provider):
        """Round-trip through pgvector: a chunk's own embedding must retrieve it."""
        await ingest_document(factory, document, SMALL_PDF, provider)

        async with factory() as db:
            probe = await db.scalar(
                select(Chunk).where(Chunk.document_id == document).order_by(Chunk.ordinal)
            )
            nearest = await db.scalar(
                select(Chunk)
                .where(Chunk.document_id == document)
                .order_by(Chunk.embedding.cosine_distance(probe.embedding))
                .limit(1)
            )
        assert nearest.id == probe.id
