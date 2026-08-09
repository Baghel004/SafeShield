"""Metrics, readiness and request correlation.

Observability code has a specific failure mode: it never breaks the feature it
watches, so nobody notices it is broken until an incident, when the dashboard
is empty and the logs cannot be correlated. These assert it actually works
while nothing is on fire.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.core import metrics
from app.core.observability import (
    REQUEST_ID_HEADER,
    JsonFormatter,
    request_id_var,
)


def _sample(name: str, **labels: str) -> float:
    """Read one counter/gauge value straight out of the registry."""
    value = metrics.REGISTRY.get_sample_value(name, labels or None)
    return value or 0.0


class TestMetricsEndpoint:
    async def test_exposes_prometheus_text_format(self, client: AsyncClient):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "safeshield_http_requests_total" in resp.text

    async def test_counts_requests_by_route_template_not_path(self, client: AsyncClient):
        """Labelling by concrete path would let one client requesting
        /api/documents/<uuid> in a loop create unbounded series and take
        Prometheus down with it."""
        reg = await client.post(
            "/api/auth/register",
            json={"email": "metrics@example.com", "password": "correct-horse-battery"},
        )
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        for _ in range(3):
            await client.get(f"/api/documents/{'0' * 8}-0000-0000-0000-{'0' * 12}", headers=headers)

        body = (await client.get("/metrics")).text
        assert 'route="/api/documents/{document_id}"' in body
        assert "0000-0000-0000" not in body, "a uuid leaked into a metric label"

    async def test_records_the_status_class(self, client: AsyncClient):
        before = _sample(
            "safeshield_http_requests_total", method="GET", route="/api/auth/me", status="4xx"
        )
        await client.get("/api/auth/me")  # 401, no token
        after = _sample(
            "safeshield_http_requests_total", method="GET", route="/api/auth/me", status="4xx"
        )
        assert after == before + 1

    async def test_unmatched_paths_share_one_series(self, client: AsyncClient):
        """Otherwise every url a scanner probes mints a new series."""
        await client.get("/definitely-not-a-route")
        await client.get("/another-one")
        body = (await client.get("/metrics")).text
        assert 'route="unmatched"' in body
        assert "definitely-not-a-route" not in body


class TestRequestId:
    async def test_every_response_carries_one(self, client: AsyncClient):
        resp = await client.get("/api/health")
        assert resp.headers.get(REQUEST_ID_HEADER)

    async def test_reuses_an_upstream_id(self, client: AsyncClient):
        """So a trace spans the proxy and the app rather than restarting here."""
        resp = await client.get("/api/health", headers={REQUEST_ID_HEADER: "upstream-123"})
        assert resp.headers[REQUEST_ID_HEADER] == "upstream-123"

    async def test_rejects_an_absurdly_long_upstream_id(self, client: AsyncClient):
        """A client-supplied value ends up in every log line, so it is bounded."""
        resp = await client.get("/api/health", headers={REQUEST_ID_HEADER: "x" * 500})
        assert resp.headers[REQUEST_ID_HEADER] != "x" * 500

    async def test_ids_differ_between_requests(self, client: AsyncClient):
        a = await client.get("/api/health")
        b = await client.get("/api/health")
        assert a.headers[REQUEST_ID_HEADER] != b.headers[REQUEST_ID_HEADER]


class TestJsonFormatter:
    def test_emits_one_json_object_per_line(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"

    def test_includes_the_current_request_id(self):
        """The whole point: a log line ten frames deep still names its request."""
        token = request_id_var.set("req-abc")
        try:
            record = logging.LogRecord("test", logging.INFO, __file__, 1, "x", (), None)
            assert json.loads(JsonFormatter().format(record))["request_id"] == "req-abc"
        finally:
            request_id_var.reset(token)

    def test_carries_extra_context_through(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "x", (), None)
        record.ctx_document_id = "doc-1"
        payload = json.loads(JsonFormatter().format(record))
        assert payload["document_id"] == "doc-1"

    def test_serialises_an_exception(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
            )
        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]


class TestReadiness:
    async def test_ready_when_dependencies_answer(self, client: AsyncClient):
        resp = await client.get("/api/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"

    async def test_503_when_a_dependency_is_down(self, client: AsyncClient):
        """A load balancer has to stop routing here, so the status code carries
        the signal -- a 200 with a sad message in the body would keep traffic
        arriving at an instance that cannot serve it."""
        with patch(
            "app.api.ops._check_redis",
            return_value=("redis", "error: ConnectionError"),
        ):
            resp = await client.get("/api/ready")

        assert resp.status_code == 503
        assert resp.json()["status"] == "not ready"

    async def test_health_stays_shallow(self, client: AsyncClient):
        """Liveness must not check dependencies. Wiring the database into it
        means a brief Postgres blip restarts every replica at once, turning a
        recoverable failure into an outage."""
        with patch("app.api.ops._check_database", side_effect=AssertionError("must not be called")):
            resp = await client.get("/api/health")
        assert resp.status_code == 200


class TestRagMetrics:
    def test_observe_retrieval_records_an_empty_result(self):
        """An empty result is still a data point. Skipping it biases the
        histogram towards the queries that happened to work."""
        before = _sample("safeshield_retrieval_top_score_count")
        metrics.observe_retrieval([], 0.01)
        assert _sample("safeshield_retrieval_top_score_count") == before + 1

    def test_observe_retrieval_attributes_each_chunk_to_a_retriever(self):
        class Chunk:
            def __init__(self, dense: int | None, sparse: int | None, score: float = 0.02) -> None:
                self.dense_rank = dense
                self.sparse_rank = sparse
                self.score = score

        before = {
            source: _sample("safeshield_retrieval_contributors_total", source=source)
            for source in ("dense", "sparse", "both")
        }

        metrics.observe_retrieval([Chunk(1, 1), Chunk(2, None), Chunk(None, 3)], 0.05)

        after = {
            source: _sample("safeshield_retrieval_contributors_total", source=source)
            for source in ("dense", "sparse", "both")
        }
        assert after["both"] == before["both"] + 1
        assert after["dense"] == before["dense"] + 1
        assert after["sparse"] == before["sparse"] + 1

    @pytest.mark.parametrize("outcome", ["grounded", "refused", "error"])
    def test_answer_outcomes_are_countable(self, outcome: str):
        before = _sample("safeshield_answers_total", outcome=outcome)
        metrics.answers.labels(outcome=outcome).inc()
        assert _sample("safeshield_answers_total", outcome=outcome) == before + 1
