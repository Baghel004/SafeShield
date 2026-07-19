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
    """Ingest one uploaded document. Retried by ARQ on failure.

    ingest_document owns its own transactions -- it must not hold one open
    across the embedding call -- so no session is opened here.
    """
    doc_id = uuid.UUID(document_id)
    path = storage_path(settings.UPLOAD_DIR, doc_id)
    return await ingest_document(SessionLocal, doc_id, path, get_embedding_provider())


def _redis_settings() -> RedisSettings:
    """Redis connection settings for the worker.

    arq defaults to a 1 second connect timeout with no retries, which is
    marginal through Docker's port proxy and over any real network -- a slow
    handshake surfaces as "Timeout connecting to server" and the worker sits
    idle while jobs queue up behind it.
    """
    rs = RedisSettings.from_dsn(settings.REDIS_URL)
    rs.conn_timeout = 10
    rs.conn_retries = 5
    rs.conn_retry_delay = 1
    return rs


class WorkerSettings:
    """Worker configuration. Start it with `python worker.py`, not the arq CLI --
    see worker.py for why."""

    functions = [ingest_document_task]
    redis_settings = _redis_settings()
    max_tries = 3
    job_timeout = 1800  # a 400-page policy can take a while
    keep_result = 3600
