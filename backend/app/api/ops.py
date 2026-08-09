"""Operational endpoints: liveness, readiness, metrics.

Liveness and readiness are deliberately different checks, and conflating them
is a classic way to build an outage. Liveness asks "is this process alive" --
if it fails, the answer is to restart. Readiness asks "can this process serve
traffic right now" -- if it fails, the answer is to stop routing to it.

Wiring a database check into liveness means a brief Postgres blip restarts
every replica simultaneously, turning a recoverable dependency failure into a
full outage. So /api/health stays shallow, and /api/ready does the real work.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core import metrics
from app.core.deps import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

# A dependency check must not hang the probe. If Postgres is wedged rather than
# down, an unbounded query would make readiness time out at the load balancer
# instead of returning a clean "not ready".
_PROBE_TIMEOUT_SECONDS = 3.0


@router.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness. Answers only "this process is running and can route."""
    return {"status": "ok", "version": "0.1.0"}


@router.get("/api/ready")
async def ready(db: DbSession, response: Response) -> dict[str, object]:
    """Readiness. Checks the dependencies a request actually needs.

    Returns 503 when a dependency is unavailable so a load balancer removes
    this instance, rather than routing requests that are certain to fail.
    """
    from app.config import settings

    probes = [_check_database(db)]
    # Only probe Redis when the deployment actually uses it. In single-process
    # mode there is no Redis, and reporting it "down" would make the host's
    # health check fail a service that is in fact healthy.
    if settings.REDIS_ENABLED:
        probes.append(_check_redis())

    results = dict(await asyncio.gather(*probes, return_exceptions=False))

    ok = all(v == "ok" for v in results.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning("readiness check failed", extra={"ctx_checks": results})

    return {"status": "ready" if ok else "not ready", "checks": results}


async def _check_database(db: DbSession) -> tuple[str, str]:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await db.execute(text("SELECT 1"))
    except TimeoutError:
        return "database", "timeout"
    except Exception as exc:  # noqa: BLE001 -- any failure means not ready
        return "database", f"error: {type(exc).__name__}"
    return "database", "ok"


async def _check_redis() -> tuple[str, str]:
    from app.config import settings

    try:
        from arq.connections import RedisSettings, create_pool

        rs = RedisSettings.from_dsn(settings.REDIS_URL)
        rs.conn_timeout = int(_PROBE_TIMEOUT_SECONDS)
        rs.conn_retries = 0

        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            pool = await create_pool(rs)
            try:
                await pool.ping()
            finally:
                await pool.aclose()
    except TimeoutError:
        return "redis", "timeout"
    except Exception as exc:  # noqa: BLE001 -- any failure means not ready
        return "redis", f"error: {type(exc).__name__}"
    return "redis", "ok"


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint.

    Not under /api: scrapers conventionally look for /metrics at the root, and
    keeping it off the API prefix makes it trivial to block at the ingress so
    it is not exposed publicly.
    """
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)
