"""Question answering over the user's policies.

Two shapes of the same pipeline:

- POST /api/chat        streams the answer as Server-Sent Events
- POST /api/chat/sync   returns it in one response

The streaming form is what the UI uses. The synchronous one exists because the
evaluation harness and the tests need a whole answer to assert on, and because
scripting against an SSE stream to check a fact is needless friction.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select

from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.ratelimit import limiter
from app.models.document import Document
from app.rag.embed import get_embedding_provider
from app.rag.retrieve import RetrievedChunk, retrieve
from app.rag.synthesize import build_citations, has_usable_context, stream_answer
from app.schemas.chat import ChatRequest, ChatResponse, CitationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def _gather_context(
    db: DbSession, payload: ChatRequest, user_id: uuid.UUID
) -> list[RetrievedChunk]:
    """Retrieve, after confirming the caller may see the requested document."""
    if payload.document_id is not None:
        visible = await db.scalar(
            select(Document).where(
                Document.id == payload.document_id,
                or_(Document.user_id == user_id, Document.user_id.is_(None)),
            )
        )
        # 404 rather than 403: a 403 would confirm the id exists.
        if visible is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Scoping is pushed into the query rather than applied to its results. A
    # post-filter asks for the global top-k and then discards everything from
    # other documents, so a question scoped to one policy returned nothing at
    # all whenever six chunks from elsewhere happened to outrank it -- even
    # when the requested document contained the answer.
    return await retrieve(
        db,
        payload.question,
        get_embedding_provider(),
        user_id=user_id,
        document_id=payload.document_id,
    )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("")
@limiter.limit(settings.RATE_LIMIT_CHAT)
async def chat(
    request: Request,  # noqa: ARG001 -- required by the rate limiter
    payload: ChatRequest,
    user: CurrentUser,
    db: DbSession,
) -> StreamingResponse:
    """Stream a grounded answer as Server-Sent Events.

    Citations are sent first, so the UI can render sources while the answer is
    still being written.
    """
    chunks = await _gather_context(db, payload, user.id)
    citations = build_citations(chunks)
    grounded = has_usable_context(chunks)

    async def events() -> AsyncIterator[str]:
        yield _sse(
            "citations",
            {
                "grounded": grounded,
                "citations": [c.__dict__ for c in citations] if grounded else [],
            },
        )
        try:
            async for token in stream_answer(payload.question, chunks):
                yield _sse("token", {"text": token})
        except Exception:
            # The response has already started, so an HTTP error status is no
            # longer possible; report it in-band instead of dying silently.
            logger.exception("answer generation failed")
            yield _sse("error", {"message": "Answer generation failed. Please try again."})
            return
        yield _sse("done", {"grounded": grounded})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # stop nginx buffering the stream
            "Connection": "keep-alive",
        },
    )


@router.post("/sync", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_CHAT)
async def chat_sync(
    request: Request,  # noqa: ARG001 -- required by the rate limiter
    payload: ChatRequest,
    user: CurrentUser,
    db: DbSession,
) -> ChatResponse:
    """Non-streaming answer, for tests, scripts and the evaluation harness."""
    chunks = await _gather_context(db, payload, user.id)
    grounded = has_usable_context(chunks)

    try:
        parts = [token async for token in stream_answer(payload.question, chunks)]
    except Exception as exc:
        # The streaming endpoint reports this in-band because its response has
        # already begun. Here nothing has been sent yet, so a proper status is
        # still available -- and 502 is accurate: the upstream model failed, not
        # the request. Without this the caller got a bare 500 and a traceback.
        logger.exception("answer generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Answer generation failed. Please try again.",
        ) from exc

    return ChatResponse(
        answer="".join(parts),
        citations=[CitationResponse(**c.__dict__) for c in build_citations(chunks)]
        if grounded
        else [],
        grounded=grounded,
    )
