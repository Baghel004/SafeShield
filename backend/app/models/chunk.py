"""Chunk model -- one embeddable unit of a document."""

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.document import Document


class Chunk(Base, TimestampMixin):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    # Denormalized from documents so every retrieval query can filter on
    # ownership without a join. NULL means the shared public corpus.
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # `content` is what was embedded (breadcrumb + body); `body` is what is shown
    # back to the user, without the breadcrumb prefix repeated.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[list[float]] = mapped_column(Vector(settings.EMBEDDING_DIMENSIONS))

    # Maintained by a database trigger; the keyword half of hybrid retrieval.
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = ({"comment": "Embedded document fragments for hybrid retrieval"},)

    def __repr__(self) -> str:
        return f"<Chunk doc={self.document_id} p{self.page_no} #{self.ordinal}>"


# Keep the column type in sync with the configured embedding model. Changing
# EMBEDDING_DIMENSIONS requires a migration, not just a config edit.
CHUNK_VECTOR_DIMENSIONS: int = settings.EMBEDDING_DIMENSIONS
