"""Prometheus metrics.

Two families, because they answer different questions.

The HTTP metrics answer "is the service up and fast" -- rate, errors, duration,
saturation. Every service has them and they are what an alert fires on.

The RAG metrics answer "is the service any *good*", which the first set cannot
see at all. A pipeline that retrieves nothing, refuses every question and still
returns 200 in 40ms looks perfect on an HTTP dashboard. These are the numbers
that would have shown the sparse retriever being silently dead for
natural-language questions, or a missing API key indexing the corpus with noise.

Cardinality is deliberately low. Labelling by full path would let one client
requesting /api/documents/<uuid> in a loop create unbounded series and take
Prometheus down, so the route *template* is used instead.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# An explicit registry rather than the global default. The worker and the API
# expose different metrics from different processes, and sharing the default
# registry makes it easy to accidentally export one from the other.
REGISTRY = CollectorRegistry()

# --- HTTP -------------------------------------------------------------------

http_requests = Counter(
    "safeshield_http_requests_total",
    "HTTP requests by method, route template and status class.",
    ["method", "route", "status"],
    registry=REGISTRY,
)

http_duration = Histogram(
    "safeshield_http_request_duration_seconds",
    "HTTP request duration.",
    ["method", "route"],
    # Tuned to this service: most endpoints answer in milliseconds, but a
    # grounded answer takes seconds, so the default buckets top out far too
    # low to show the shape of the tail that matters.
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)

http_in_flight = Gauge(
    "safeshield_http_requests_in_flight",
    "Requests currently being served.",
    registry=REGISTRY,
)

# --- retrieval --------------------------------------------------------------

retrieval_duration = Histogram(
    "safeshield_retrieval_duration_seconds",
    "Hybrid retrieval, including the query embedding call.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
    registry=REGISTRY,
)

retrieval_chunks = Histogram(
    "safeshield_retrieval_chunks",
    "Chunks returned per query.",
    buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10),
    registry=REGISTRY,
)

retrieval_top_score = Histogram(
    "safeshield_retrieval_top_score",
    "Fused RRF score of the best chunk. Near zero means nothing matched.",
    buckets=(0.0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.05),
    registry=REGISTRY,
)

retrieval_contributors = Counter(
    "safeshield_retrieval_contributors_total",
    "Which retriever found each returned chunk. If 'sparse' flatlines the "
    "lexical half has broken, which is invisible from latency alone.",
    ["source"],  # dense | sparse | both
    registry=REGISTRY,
)

# --- answers ----------------------------------------------------------------

answers = Counter(
    "safeshield_answers_total",
    "Answers by outcome. A rising refusal rate means retrieval is degrading.",
    ["outcome"],  # grounded | refused | error
    registry=REGISTRY,
)

answer_duration = Histogram(
    "safeshield_answer_duration_seconds",
    "Time to complete an answer, retrieval included.",
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34),
    registry=REGISTRY,
)

answer_ttft = Histogram(
    "safeshield_answer_time_to_first_token_seconds",
    "Time to the first streamed token -- what the user actually waits for.",
    buckets=(0.25, 0.5, 1, 1.5, 2, 3, 5, 8),
    registry=REGISTRY,
)

# --- cost -------------------------------------------------------------------

openai_tokens = Counter(
    "safeshield_openai_tokens_total",
    "Tokens billed by OpenAI. The only metric here that maps to money, and "
    "the one that turns a runaway loop into an alert instead of an invoice.",
    ["model", "kind"],  # kind: prompt | completion | embedding
    registry=REGISTRY,
)

openai_errors = Counter(
    "safeshield_openai_errors_total",
    "Failed OpenAI calls by operation.",
    ["operation"],  # embedding | chat
    registry=REGISTRY,
)

# --- ingestion --------------------------------------------------------------

ingestions = Counter(
    "safeshield_ingestions_total",
    "Document ingestion outcomes.",
    ["outcome"],  # succeeded | failed
    registry=REGISTRY,
)

ingestion_duration = Histogram(
    "safeshield_ingestion_duration_seconds",
    "End-to-end ingestion of one document.",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

ingestion_chunks = Counter(
    "safeshield_ingestion_chunks_total",
    "Chunks written to the index.",
    registry=REGISTRY,
)

documents_pending = Gauge(
    "safeshield_documents_pending",
    "Documents stuck in pending. Sustained non-zero means jobs are not being "
    "picked up -- the failure the requeue sweep exists to correct.",
    registry=REGISTRY,
)

queue_depth = Gauge(
    "safeshield_queue_depth",
    "Jobs waiting in the ARQ queue.",
    registry=REGISTRY,
)


def observe_retrieval(chunks: list[object], seconds: float) -> None:
    """Record one retrieval. Kept here so the call site stays one line."""
    retrieval_duration.observe(seconds)
    retrieval_chunks.observe(len(chunks))

    if not chunks:
        # An empty result still means "top score zero" -- omitting it would
        # bias the histogram towards the queries that worked.
        retrieval_top_score.observe(0.0)
        return

    top = chunks[0]
    retrieval_top_score.observe(float(getattr(top, "score", 0.0)))

    for chunk in chunks:
        dense = getattr(chunk, "dense_rank", None)
        sparse = getattr(chunk, "sparse_rank", None)
        source = "both" if dense and sparse else "dense" if dense else "sparse"
        retrieval_contributors.labels(source=source).inc()
