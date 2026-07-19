"""Answer generation, grounded in retrieved policy text.

The model is given numbered excerpts and told to answer only from them. This is
not politeness -- an insurance answer that sounds authoritative and is wrong is
worse than no answer, because someone may act on it financially. Three things
enforce that:

- The excerpts are the only source. The prompt forbids outside knowledge.
- Every claim must carry a [n] citation, so an answer can be checked against
  the policy rather than trusted.
- When retrieval finds nothing relevant, the model refuses instead of reaching.
  Refusing is a correct answer to "the policy does not say".
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import settings
from app.rag.retrieve import RetrievedChunk

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You answer questions about insurance policy documents.

Rules, in order of importance:

1. Answer ONLY from the numbered excerpts provided. You have no other knowledge \
of these policies. Never infer, generalise from typical policies, or fill gaps.
2. Cite every factual claim with the excerpt number in square brackets, like \
[1] or [2][3]. A sentence stating a fact without a citation is a failure.
3. If the excerpts do not contain the answer, say exactly: "I couldn't find this \
in the provided documents." Then say what you did find, if anything is close. \
Do not guess, and do not apologise at length.
4. Quote figures, limits, waiting periods and percentages exactly as written. \
Never round, convert, or restate a number in your own words.
5. If excerpts disagree, or a limit depends on the plan variant, say so and cite \
each one. Policies differ per plan; presenting one plan's number as universal is \
a factual error.
6. Be direct. Lead with the answer, then the supporting detail.

You are not a licensed advisor. Do not tell the user what to buy, what to claim, \
or what they are entitled to -- report what the document says."""

NO_CONTEXT_ANSWER = "I couldn't find this in the provided documents."


@dataclass(frozen=True)
class Citation:
    """A source the answer referred to, by its [n] index."""

    index: int
    document_id: str
    filename: str
    page_no: int
    section_path: str
    excerpt: str


def build_citations(chunks: list[RetrievedChunk], excerpt_chars: int = 400) -> list[Citation]:
    return [
        Citation(
            index=i,
            document_id=str(c.document_id),
            filename=c.filename,
            page_no=c.page_no,
            section_path=c.section_path,
            excerpt=c.body[:excerpt_chars].strip(),
        )
        for i, c in enumerate(chunks, start=1)
    ]


def build_user_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    """Number the excerpts so the model's [n] citations map back to sources."""
    blocks = []
    for i, c in enumerate(chunks, start=1):
        where = f"{c.filename}, page {c.page_no}"
        if c.section_path:
            where += f", section: {c.section_path}"
        blocks.append(f"[{i}] ({where})\n{c.body}")

    return (
        "Excerpts from the policy documents:\n\n"
        + "\n\n".join(blocks)
        + f"\n\n---\n\nQuestion: {question}"
    )


def has_usable_context(chunks: list[RetrievedChunk]) -> bool:
    """Whether retrieval found anything worth sending to the model.

    Checked before spending a request: if the best fused score is near zero,
    nothing matched on either retriever and the answer would be invented.
    """
    return bool(chunks) and chunks[0].score >= settings.MIN_RETRIEVAL_SCORE


@lru_cache(maxsize=1)
def _client() -> AsyncOpenAI:
    """One client for the process, not one per request.

    Each `AsyncOpenAI()` builds its own connection pool, so constructing one per
    question meant a fresh TLS handshake on every answer. The timeout is
    explicit because the SDK default is 600s, and an SSE connection held open
    for ten minutes is indistinguishable from a hang to the user.
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.OPENAI_TIMEOUT_SECONDS,
        max_retries=settings.OPENAI_MAX_RETRIES,
    )


async def stream_answer(question: str, chunks: list[RetrievedChunk]) -> AsyncIterator[str]:
    """Yield the answer incrementally.

    Streaming is not a nicety here: a grounded answer over six excerpts takes
    several seconds, and a user watching a spinner assumes the app is broken.
    """
    if not has_usable_context(chunks):
        yield NO_CONTEXT_ANSWER
        return

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot generate answers")

    stream = await _client().chat.completions.create(
        model=settings.CHAT_MODEL,
        max_tokens=settings.ANSWER_MAX_TOKENS,
        # Low but non-zero: this is extraction, not composition. Wording should
        # track the source document, not the model's preferences.
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(question, chunks)},
        ],
        stream=True,
    )

    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
