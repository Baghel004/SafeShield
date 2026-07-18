"""Documents, chunks, pgvector and full-text search

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small; keep in sync with settings


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # create_type=False on the column reference below: the type is created here
    # once, explicitly. Without it, create_table emits a second CREATE TYPE and
    # the migration dies with "type document_status already exists".
    document_status = postgresql.ENUM(
        "pending", "processing", "ready", "failed", name="document_status", create_type=False
    )
    document_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("status", document_status, nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("section_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        comment="Embedded document fragments for hybrid retrieval",
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_user_id", "chunks", ["user_id"])

    # Keyword half of hybrid retrieval. A trigger keeps tsv in step with content;
    # a GENERATED column cannot be used here because the weighting combines two
    # columns and we may tune it later without rewriting the table definition.
    op.execute(
        """
        CREATE FUNCTION chunks_tsv_update() RETURNS trigger AS $$
        BEGIN
            NEW.tsv :=
                setweight(to_tsvector('english', coalesce(NEW.section_path, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.body, '')), 'B');
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_tsv_trigger
        BEFORE INSERT OR UPDATE OF section_path, body ON chunks
        FOR EACH ROW EXECUTE FUNCTION chunks_tsv_update();
        """
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")

    # HNSW over cosine distance. Built empty, so index construction is cheap here
    # and rows are indexed as they are inserted.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.execute("DROP TRIGGER IF EXISTS chunks_tsv_trigger ON chunks")
    op.execute("DROP FUNCTION IF EXISTS chunks_tsv_update()")
    op.drop_index("ix_chunks_user_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_table("documents")
    postgresql.ENUM(name="document_status").drop(op.get_bind(), checkfirst=True)
