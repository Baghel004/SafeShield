"""Per-client rate limiting.

Exists to protect the LLM budget more than to stop abuse: every question costs
real money, so a script in a loop is a bill, not just load. Limits are generous
enough that a human never encounters them.

Backed by Redis so the limit holds across replicas. Falls back to in-process
counters when Redis is unavailable -- a degraded limit is better than the
request failing, and better than no limit at all.
"""

from __future__ import annotations

import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

logger = logging.getLogger(__name__)


def client_key(request: Request) -> str:
    """Identify the caller.

    Prefers the authenticated user so that several people behind one NAT are not
    throttled as a single client, and so a user cannot reset their budget by
    changing IP. Falls back to the remote address for anonymous requests.
    """
    user = getattr(request.state, "user_id", None)
    if user:
        return f"user:{user}"
    return f"ip:{get_remote_address(request)}"


def _storage_uri() -> str | None:
    # No Redis URI in single-process mode: the limiter then keeps its counters
    # in memory. That is not merely a fallback but the correct choice there --
    # with one instance there is nothing to share state across, so an in-memory
    # window is exactly right and needs no external service.
    if not settings.RATE_LIMIT_ENABLED or not settings.REDIS_ENABLED:
        return None
    return settings.REDIS_URL


limiter = Limiter(
    key_func=client_key,
    storage_uri=_storage_uri(),
    enabled=settings.RATE_LIMIT_ENABLED,
    # Without this a Redis outage would reject every request.
    in_memory_fallback_enabled=True,
    strategy="fixed-window",
)
