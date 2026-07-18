"""Document upload and management.

Upload returns 202 immediately and hands the work to the background worker; the
client polls GET /api/documents/{id} until status is `ready` or `failed`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import or_, select

from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.files import (
    InvalidUploadError,
    sanitize_filename,
    storage_path,
    validate_pdf_bytes,
)
from app.models.document import Document, DocumentStatus
from app.schemas.document import DocumentListResponse, DocumentResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _to_response(document: Document) -> DocumentResponse:
    payload = DocumentResponse.model_validate(document)
    return payload.model_copy(update={"is_shared": document.user_id is None})


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> DocumentResponse:
    data = await file.read()
    try:
        validate_pdf_bytes(data, settings.MAX_UPLOAD_BYTES)
    except InvalidUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    display_name = sanitize_filename(file.filename)
    document = Document(
        user_id=user.id,
        filename=display_name,
        title=display_name.removesuffix(".pdf"),
        status=DocumentStatus.PENDING,
        size_bytes=len(data),
    )
    db.add(document)
    await db.flush()

    # Path is generated from the document id, so the client cannot influence it.
    storage_path(settings.UPLOAD_DIR, document.id).write_bytes(data)

    await _enqueue_ingestion(document.id)
    return _to_response(document)


async def _enqueue_ingestion(document_id: uuid.UUID) -> None:
    """Queue the ingestion job.

    A queue outage must not lose the upload: the document row is already saved as
    `pending`, so the job can be re-driven later.
    """
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        # Short, non-retrying timeout: if the queue is unreachable, the upload
        # should still return promptly. The default would stall every upload for
        # tens of seconds during a Redis outage.
        redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
        redis_settings.conn_timeout = 2
        redis_settings.conn_retries = 1

        pool = await create_pool(redis_settings)
        try:
            await pool.enqueue_job("ingest_document_task", str(document_id))
        finally:
            await pool.aclose()
    except Exception:
        logger.warning(
            "Could not enqueue ingestion for %s; document left pending", document_id, exc_info=True
        )


@router.get("", response_model=DocumentListResponse)
async def list_documents(user: CurrentUser, db: DbSession) -> DocumentListResponse:
    """The caller's own documents plus the shared sample corpus."""
    result = await db.scalars(
        select(Document)
        .where(or_(Document.user_id == user.id, Document.user_id.is_(None)))
        .order_by(Document.created_at.desc())
    )
    return DocumentListResponse(documents=[_to_response(d) for d in result.all()])


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> DocumentResponse:
    document = await _get_visible(db, document_id, user.id)
    return _to_response(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    document = await _get_visible(db, document_id, user.id)
    if document.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Shared sample documents cannot be deleted",
        )

    await db.delete(document)  # chunks cascade

    path = storage_path(settings.UPLOAD_DIR, document_id)
    path.unlink(missing_ok=True)


async def _get_visible(db: DbSession, document_id: uuid.UUID, user_id: uuid.UUID) -> Document:
    """Fetch a document the caller is allowed to see.

    A document belonging to someone else returns 404, not 403 -- a 403 would
    confirm that the id exists.
    """
    document = await db.scalar(
        select(Document).where(
            Document.id == document_id,
            or_(Document.user_id == user_id, Document.user_id.is_(None)),
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document
