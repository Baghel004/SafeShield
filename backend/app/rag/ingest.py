"""Document ingestion: PDF on disk -> embedded chunks in Postgres.

Pure of any web or queue concerns so it can be called from the ARQ worker, a
seed script, or a test without change.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document, DocumentStatus
from app.rag.chunk import chunk_blocks
from app.rag.embed import EmbeddingProvider
from app.rag.extract import extract_blocks

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Ingestion failed for a reason worth showing the user."""


async def ingest_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    pdf_path: str | Path,
    provider: EmbeddingProvider,
) -> int:
    """Extract, chunk, embed and store one document. Returns the chunk count.

    Marks the document `ready` on success and `failed` (with the reason) on
    error, so a stuck upload is always visible to the user rather than silently
    remaining `pending` forever.
    """
    document = await db.scalar(select(Document).where(Document.id == document_id))
    if document is None:
        raise IngestionError(f"Document {document_id} not found")

    try:
        document.status = DocumentStatus.PROCESSING
        await db.flush()

        blocks = extract_blocks(pdf_path)
        if not blocks:
            raise IngestionError(
                "No text could be extracted. The PDF may be a scan; OCR is not supported."
            )

        chunks = chunk_blocks(blocks)
        if not chunks:
            raise IngestionError("Document produced no indexable content")

        vectors = await provider.embed([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise IngestionError(
                f"Embedding count mismatch: {len(vectors)} vectors for {len(chunks)} chunks"
            )

        # Re-ingestion replaces prior chunks rather than duplicating them.
        await db.execute(delete(Chunk).where(Chunk.document_id == document_id))

        db.add_all(
            [
                Chunk(
                    document_id=document_id,
                    user_id=document.user_id,
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

        document.page_count = max((b.page_no for b in blocks), default=0)
        document.chunk_count = len(chunks)
        document.status = DocumentStatus.READY
        document.error = None
        await db.flush()

        logger.info(
            "Ingested %s: %d pages, %d chunks", document.filename, document.page_count, len(chunks)
        )
        return len(chunks)

    except Exception as exc:
        document.status = DocumentStatus.FAILED
        document.error = str(exc)[:2000]
        await db.flush()
        logger.exception("Ingestion failed for document %s", document_id)
        raise
