"""Embedding providers.

The provider is an interface with two implementations so the ingestion pipeline
can be exercised end-to-end -- extract, chunk, store, retrieve -- without an API
key or network access. CI runs the whole path against the deterministic fake;
only the vectors differ.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class MissingEmbeddingKeyError(RuntimeError):
    """Raised when production is configured without an embedding API key."""

    def __init__(self) -> None:
        super().__init__(
            "OPENAI_API_KEY is not set and ENV=prod. Refusing to fall back to "
            "FakeEmbeddings: it would index the corpus with deterministic noise "
            "and report every document as ready."
        )


class EmbeddingProvider(Protocol):
    """Turns text into vectors of `dimensions` length."""

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddings:
    """Batched embeddings via the OpenAI API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        key = api_key or settings.OPENAI_API_KEY
        if not key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Set it in the environment, or use "
                "FakeEmbeddings for tests."
            )
        from openai import AsyncOpenAI

        # Both explicit: the SDK's 600s default would let one hung batch hold an
        # ARQ worker slot until the job timeout, and retry behaviour that matters
        # for cost and latency should be a decision, not an inherited default.
        self._client = AsyncOpenAI(
            api_key=key,
            timeout=settings.OPENAI_TIMEOUT_SECONDS,
            max_retries=settings.OPENAI_MAX_RETRIES,
        )
        self._model = model or settings.EMBEDDING_MODEL
        self._dimensions = dimensions or settings.EMBEDDING_DIMENSIONS
        self._batch_size = batch_size or settings.EMBEDDING_BATCH_SIZE

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(
                model=self._model, input=batch, dimensions=self._dimensions
            )
            # The API documents order preservation, but the cost of being wrong
            # here is silently mismatching vectors to chunks -- so sort by index.
            ordered = sorted(response.data, key=lambda d: d.index)
            out.extend(item.embedding for item in ordered)

        if len(out) != len(texts):
            raise RuntimeError(f"Embedding count mismatch: got {len(out)}, expected {len(texts)}")
        return out


class FakeEmbeddings:
    """Deterministic embeddings derived from the text itself.

    Not semantically meaningful, but stable and unit-norm, so identical text
    yields identical vectors and similarity search is exercised for real.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        self._dimensions = dimensions or settings.EMBEDDING_DIMENSIONS

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Stretch the digest deterministically to the required width.
        raw: list[float] = []
        counter = 0
        while len(raw) < self._dimensions:
            block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
            raw.extend(b / 255.0 - 0.5 for b in block)
            counter += 1
        vec = raw[: self._dimensions]

        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured provider.

    Falls back to the fake only outside production. The fallback is genuinely
    useful -- CI exercises the entire ingestion path without a key -- but it is
    silent by nature: fake vectors index cleanly, every document ends up
    `ready`, and retrieval then returns noise with no error raised anywhere. A
    misconfigured production deployment would look completely healthy while
    answering from nothing, so there it is a startup failure instead.
    """
    if settings.OPENAI_API_KEY:
        return OpenAIEmbeddings()
    if settings.ENV == "prod":
        raise MissingEmbeddingKeyError
    logger.warning(
        "OPENAI_API_KEY not set -- using FakeEmbeddings. Vectors are deterministic "
        "noise and retrieval results are meaningless. Development and tests only."
    )
    return FakeEmbeddings()
