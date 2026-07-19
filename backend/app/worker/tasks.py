"""Background ingestion worker (ARQ).

Ingestion is minutes of work for a large policy. Running it inside the upload
request would hold a worker, exceed the load balancer timeout, and stall every
other request, so the endpoint enqueues and returns 202 immediately.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.constants import default_queue_name
from sqlalchemy import func, select

from app.config import settings
from app.core import metrics
from app.core.files import storage_path
from app.db import SessionLocal
from app.models.document import Document, DocumentStatus
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


async def requeue_stranded_documents(ctx: dict[str, Any]) -> int:
    """Re-enqueue uploads whose ingestion job was never queued.

    The upload endpoint deliberately does not fail when Redis is unreachable --
    the bytes are on disk and the row is saved, so losing the upload would be a
    worse outcome than a delayed one. But that only holds if something actually
    re-drives the work, and previously nothing did: a Redis blip during upload
    stranded the document in `pending` forever, with the API reporting it as
    still processing.

    The age threshold is what keeps this from fighting the normal path. A
    document that was enqueued seconds ago is legitimately pending and must not
    be queued a second time.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.INGEST_STRANDED_AFTER_SECONDS)

    async with SessionLocal() as session, session.begin():
        stranded = (
            await session.scalars(
                select(Document.id).where(
                    Document.status == DocumentStatus.PENDING,
                    Document.created_at < cutoff,
                )
            )
        ).all()
        # Every pending document, not only the stale ones. Sustained non-zero
        # is the signal that jobs are not being picked up at all -- visible on
        # a dashboard well before anyone notices an upload never finished.
        pending_total = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.status == DocumentStatus.PENDING)
        )

    metrics.documents_pending.set(pending_total or 0)

    redis = ctx["redis"]
    try:
        metrics.queue_depth.set(await redis.llen(default_queue_name))
    except Exception:  # noqa: BLE001 -- a metric must never break the sweep
        logger.debug("could not read queue depth", exc_info=True)

    if not stranded:
        return 0

    # Reuse the worker's own pool rather than opening a connection per document.
    for doc_id in stranded:
        await redis.enqueue_job("ingest_document_task", str(doc_id))

    logger.warning("Re-enqueued %d document(s) stranded in pending: %s", len(stranded), stranded)
    return len(stranded)


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


async def _startup(_ctx: dict[str, Any]) -> None:
    """Serve worker metrics.

    The worker has no HTTP server, so its ingestion counters would be invisible
    to Prometheus -- and ingestion is where the slow, expensive and most
    failure-prone work happens. `start_http_server` runs a small server on its
    own thread, which is enough for a scrape endpoint.
    """
    from prometheus_client import start_http_server

    start_http_server(settings.WORKER_METRICS_PORT, registry=metrics.REGISTRY)
    logger.info("worker metrics on :%d/metrics", settings.WORKER_METRICS_PORT)


class WorkerSettings:
    """Worker configuration. Start it with `python worker.py`, not the arq CLI --
    see worker.py for why."""

    functions = [ingest_document_task]
    on_startup = _startup
    # Runs on one worker at a time regardless of how many replicas are up, so
    # scaling out does not multiply the sweep.
    cron_jobs = [cron(requeue_stranded_documents, minute=set(range(0, 60, 5)), run_at_startup=True)]
    redis_settings = _redis_settings()
    max_tries = 3
    job_timeout = 1800  # a 400-page policy can take a while
    keep_result = 3600
