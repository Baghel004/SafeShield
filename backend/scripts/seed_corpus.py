"""Ingest the bundled sample policies as the shared public corpus.

These documents get `user_id = NULL`, which makes them searchable by every user
(including the demo account) while remaining undeletable through the API.

Usage:
    python scripts/seed_corpus.py            # skip documents already ingested
    python scripts/seed_corpus.py --force    # re-ingest everything
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.compat import asyncio_run  # noqa: E402
from app.core.storage import get_storage  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.rag.embed import get_embedding_provider  # noqa: E402
from app.rag.ingest import ingest_document  # noqa: E402

DATASETS = Path(__file__).resolve().parents[2] / "datasets"

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
logger = logging.getLogger("seed")


async def seed(force: bool) -> int:
    # Imported here so the event loop policy is set before the engine is built.
    from app.db import SessionLocal, engine

    pdfs = sorted(DATASETS.glob("*.pdf"))
    if not pdfs:
        logger.error("No PDFs found in %s", DATASETS)
        return 1

    provider = get_embedding_provider()
    logger.info("Provider: %s (%d dims)", type(provider).__name__, provider.dimensions)

    total_chunks = 0
    async with SessionLocal() as db:
        for pdf in pdfs:
            existing = await db.scalar(
                select(Document).where(Document.filename == pdf.name, Document.user_id.is_(None))
            )
            if existing and existing.status == DocumentStatus.READY and not force:
                logger.info("SKIP  %-46s (%d chunks)", pdf.name, existing.chunk_count)
                total_chunks += existing.chunk_count
                continue

            document = existing or Document(
                user_id=None,  # shared corpus
                filename=pdf.name,
                title=pdf.stem,
                status=DocumentStatus.PENDING,
                size_bytes=pdf.stat().st_size,
            )
            if not existing:
                db.add(document)
            await db.flush()

            # Copy into the configured store so re-ingestion works the same
            # way it does for a user upload -- including via S3 when that is
            # what the deployment uses.
            storage = get_storage()
            storage.put(document.id, pdf.read_bytes())

            await db.commit()  # release before the slow work

            logger.info("INGEST %s ...", pdf.name)
            with storage.as_local_path(document.id) as dest:
                count = await ingest_document(SessionLocal, document.id, dest, provider)
            total_chunks += count
            await db.refresh(document)
            logger.info("  -> %d chunks, %d pages", count, document.page_count)

    await engine.dispose()
    logger.info("Done. %d chunks across %d documents.", total_chunks, len(pdfs))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-ingest already-ready documents")
    args = parser.parse_args()

    return asyncio_run(seed(args.force))


if __name__ == "__main__":
    raise SystemExit(main())
