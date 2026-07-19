"""Chat request/response schemas."""

import uuid

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    # Restrict the search to one document; omit to search everything visible.
    document_id: uuid.UUID | None = None


class CitationResponse(BaseModel):
    index: int
    document_id: str
    filename: str
    page_no: int
    section_path: str
    excerpt: str


class ChatResponse(BaseModel):
    """Non-streaming form, used by tests and the eval harness."""

    answer: str
    citations: list[CitationResponse]
    grounded: bool
