"""Request context, structured logging and HTTP instrumentation.

The audit of the earlier phases found that a failed answer and a stranded
upload were both undiagnosable in production: plain-text logs with no
correlation, and no way to tie a log line to the request that produced it. This
module is the fix -- every log line carries a request id, and the same id comes
back in the response header, so a user reporting "it failed" hands you the
exact key to find it.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core import metrics

# A context variable rather than a parameter threaded through every function:
# it follows the async task, so a log call ten frames deep inside retrieval
# still knows which request it belongs to without any plumbing.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
REQUEST_ID_HEADER = "X-Request-ID"


def current_request_id() -> str:
    return request_id_var.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Plain text is easier to read over one shoulder; JSON is the only format a
    log aggregator can filter on. Since the whole point here is answering
    "what happened to request X", structure wins.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed via `extra=` rides along, so a call site can attach
        # a document id or a chunk count without inventing a message format.
        for key, value in record.__dict__.items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value

        return json.dumps(payload, default=str)


def configure_logging(*, debug: bool, json_logs: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # These log every request themselves; the middleware below already does it
    # with far more context, and two lines per request is noise.
    for noisy in ("uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def route_template(request: Request) -> str:
    """The route pattern, not the concrete path.

    `/api/documents/{document_id}` rather than the uuid. Labelling metrics by
    the real path lets one client create unbounded label values and take
    Prometheus down with it.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    # No matched route means a 404: bucket them all together rather than
    # minting a series for every url someone probes.
    return "unmatched"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Assign a request id, record metrics, log the outcome."""

    def __init__(self, app: ASGIApp, *, logger_name: str = "safeshield.access") -> None:
        super().__init__(app)
        self._log = logging.getLogger(logger_name)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Honour an upstream id if a proxy or the client set one, so a trace
        # spans the whole path rather than restarting here.
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        started = time.perf_counter()
        metrics.http_in_flight.inc()
        status = 500

        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            # Record and log before re-raising: an unhandled exception is
            # exactly the case where the metric and the id matter most, and
            # without this it would be the one request that goes unmeasured.
            self._log.exception(
                "request failed",
                extra={"ctx_method": request.method, "ctx_path": request.url.path},
            )
            raise
        finally:
            elapsed = time.perf_counter() - started
            route = route_template(request)
            metrics.http_in_flight.dec()
            metrics.http_duration.labels(method=request.method, route=route).observe(elapsed)
            metrics.http_requests.labels(
                method=request.method,
                route=route,
                # The status *class*: five series instead of one per code, and
                # nothing is ever alerted on 418 specifically.
                status=f"{status // 100}xx",
            ).inc()

            self._log.info(
                "request",
                extra={
                    "ctx_method": request.method,
                    "ctx_path": request.url.path,
                    "ctx_route": route,
                    "ctx_status": status,
                    "ctx_duration_ms": round(elapsed * 1000, 2),
                },
            )
            request_id_var.reset(token)
