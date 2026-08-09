"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api import auth, chat, documents, ops
from app.config import settings
from app.core.observability import ObservabilityMiddleware, configure_logging
from app.core.ratelimit import limiter
from app.db import engine

configure_logging(debug=settings.DEBUG, json_logs=settings.JSON_LOGS)
logger = logging.getLogger("safeshield")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("SafeShield API starting (env=%s)", settings.ENV)
    yield
    await engine.dispose()
    logger.info("SafeShield API stopped")


app = FastAPI(
    title="SafeShield API",
    description="RAG-powered insurance policy assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter


def _on_rate_limit(request: Request, exc: Exception) -> Response:
    """Adapter for slowapi's handler.

    Starlette types exception handlers as taking `Exception`; slowapi's is
    narrowed to `RateLimitExceeded`. The cast is safe because it is only
    registered for that exception type.
    """
    return _rate_limit_exceeded_handler(request, cast(RateLimitExceeded, exc))


app.add_exception_handler(RateLimitExceeded, _on_rate_limit)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,  # required for the refresh cookie
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Registered last so it runs outermost: Starlette applies middleware in reverse
# order of registration. That matters -- a request rejected by the rate limiter
# or CORS would otherwise never be counted, and those are exactly the requests
# worth seeing on a dashboard.
app.add_middleware(ObservabilityMiddleware)

app.include_router(ops.router)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(chat.router)
