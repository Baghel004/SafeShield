"""Document ingestion: PDF on disk -> embedded chunks in Postgres.

Pure of any web or queue concerns so it can be called from the ARQ worker, a
seed script, or a test without change.

Transaction shape matters here. Extraction and embedding take minutes for a
large policy, and embedding is a network call to a third party. Holding a
database transaction open across that gets the connection closed underneath you
by any managed Postgres (Neon, RDS Proxy, PgBouncer) and wastes a pooled
connection while doing no database work. So the slow parts run with no session
open at all, bracketed by two short transactions.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.rag.chunk import Chunk as TextChunk
from app.rag.chunk import chunk_blocks
from app.rag.embed import EmbeddingProvider
from app.rag.extract import extract_blocks

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Ingestion failed for a reason worth showing the user."""


async def ingest_document(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    pdf_path: str | Path,
    provider: EmbeddingProvider,
) -> int:
    """Extract, chunk, embed and store one document. Returns the chunk count.

    Marks the document `ready` on success and `failed` (with the reason) on
    error, so a stuck upload is always visible rather than sitting on `pending`
    forever.
    """
    # --- Transaction 1: claim the document -------------------------------
    async with session_factory() as db:
        document = await db.scalar(select(Document).where(Document.id == document_id))
        if document is None:
            raise IngestionError(f"Document {document_id} not found")
        owner_id = document.user_id
        filename = document.filename
        document.status = DocumentStatus.PROCESSING
        await db.commit()

    # --- No transaction held: the slow work ------------------------------
    try:
        blocks = extract_blocks(pdf_path)
        if not blocks:
            raise IngestionError(
                "No text could be extracted. The PDF may be a scan; OCR is not supported."
            )

        chunks: list[TextChunk] = chunk_blocks(blocks)
        if not chunks:
            raise IngestionError("Document produced no indexable content")

        vectors = await provider.embed([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise IngestionError(
                f"Embedding count mismatch: {len(vectors)} vectors for {len(chunks)} chunks"
            )
        page_count = max((b.page_no for b in blocks), default=0)

    except Exception as exc:
        await _mark_failed(session_factory, document_id, exc)
        raise

    # --- Transaction 2: persist ------------------------------------------
    try:
        async with session_factory() as db:
            # Re-ingestion replaces prior chunks rather than duplicating them.
            await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            db.add_all(
                [
                    Chunk(
                        document_id=document_id,
                        user_id=owner_id,
                        ordinal=i,
                        page_no=c.page_no,
                        section_path=c.section_path,
                        content=c.content,
                        body=c.body,
                        token_count=c.token_count,
                        embedding=vec,
                    )
                    for i, (c, vec) in enumerate(zip(chunks, vectors, strict=True))
                ]
            )
            document = await db.scalar(select(Document).where(Document.id == document_id))
            if document is not None:
                document.page_count = page_count
                document.chunk_count = len(chunks)
                document.status = DocumentStatus.READY
                document.error = None
            await db.commit()
    except Exception as exc:
        await _mark_failed(session_factory, document_id, exc)
        raise

    logger.info("Ingested %s: %d pages, %d chunks", filename, page_count, len(chunks))
    return len(chunks)


async def _mark_failed(
    session_factory: async_sessionmaker[AsyncSession],
    document_id: uuid.UUID,
    exc: Exception,
) -> None:
    """Record the failure on a fresh session.

    A fresh one because the session that failed may itself be the problem -- a
    dropped connection cannot be used to report that the connection dropped.
    """
    logger.exception("Ingestion failed for document %s", document_id)
    try:
        async with session_factory() as db:
            document = await db.scalar(select(Document).where(Document.id == document_id))
            if document is not None:
                document.status = DocumentStatus.FAILED
                document.error = str(exc)[:2000]
                await db.commit()
    except Exception:
        logger.exception("Could not record failure state for document %s", document_id)
