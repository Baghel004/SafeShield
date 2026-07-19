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

from app.api import auth, documents
from app.config import settings
from app.core.ratelimit import limiter
from app.db import engine

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
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

app.include_router(auth.router)
app.include_router(documents.router)


@app.get("/api/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}
