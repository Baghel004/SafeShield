"""Background ingestion worker (ARQ).

Ingestion is minutes of work for a large policy. Running it inside the upload
request would hold a worker, exceed the load balancer timeout, and stall every
other request, so the endpoint enqueues and returns 202 immediately.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from arq.connections import RedisSettings

from app.config import settings
from app.core.files import storage_path
from app.db import SessionLocal
from app.rag.embed import get_embedding_provider
from app.rag.ingest import ingest_document

logger = logging.getLogger(__name__)


async def ingest_document_task(ctx: dict[str, Any], document_id: str) -> int:
    """Ingest one uploaded document. Retried by ARQ on failure."""
    doc_id = uuid.UUID(document_id)
    path = storage_path(settings.UPLOAD_DIR, doc_id)

    async with SessionLocal() as db:
        try:
            count = await ingest_document(db, doc_id, path, get_embedding_provider())
            await db.commit()
        except Exception:
            # ingest_document already recorded status=failed on its own session
            # state; commit that so the user sees the failure instead of a
            # document stuck on "processing".
            await db.commit()
            raise
    return count


class WorkerSettings:
    """ARQ entrypoint: `arq app.worker.tasks.WorkerSettings`."""

    functions = [ingest_document_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_tries = 3
    job_timeout = 1800  # a 400-page policy can take a while
    keep_result = 3600
