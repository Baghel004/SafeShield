"""Document request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    title: str | None
    status: DocumentStatus
    error: str | None
    page_count: int
    chunk_count: int
    size_bytes: int
    created_at: datetime
    is_shared: bool = False


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
