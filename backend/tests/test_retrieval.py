"""Hybrid retrieval and answer grounding.

Retrieval runs against real chunks in pgvector. Embeddings are the deterministic
fake -- dense similarity is then meaningless, but everything that matters here is
not: the fusion arithmetic, the sparse (real tsvector) half, and above all the
ownership boundary.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.rag.embed import FakeEmbeddings
from app.rag.ingest import ingest_document
from app.rag.retrieve import RetrievedChunk, retrieve
from app.rag.synthesize import (
    NO_CONTEXT_ANSWER,
    build_citations,
    build_user_prompt,
    has_usable_context,
    stream_answer,
)
from tests.conftest import needs_pgvector

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
SMALL_PDF = DATASETS / "EDLHLGA23009V012223.pdf"

needs_datasets = pytest.mark.skipif(not SMALL_PDF.exists(), reason="sample PDFs not present")


def _chunk(**kw: object) -> RetrievedChunk:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "filename": "policy.pdf",
        "page_no": 3,
        "section_path": "4. Exclusions",
        "body": "Cosmetic surgery is not covered.",
        "token_count": 20,
        "dense_rank": 1,
        "sparse_rank": 1,
        "score": 0.5,
    }
    return RetrievedChunk(**{**base, **kw})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Retrieval against real data
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("engine")
@needs_pgvector
@needs_datasets
class TestHybridRetrieval:
    @pytest.fixture
    async def corpus(self, engine):
        """A shared document plus one owned by another user, both indexed."""
        factory = async_sessionmaker(engine, expire_on_commit=False)
        provider = FakeEmbeddings()

        async with factory() as db:
            other = User(email=f"other-{uuid.uuid4()}@example.com", password_hash="x")
            db.add(other)
            await db.commit()
            other_id = other.id

        made: list[uuid.UUID] = []
        for owner in (None, other_id):
            async with factory() as db:
                doc = Document(
                    user_id=owner,
                    filename=SMALL_PDF.name,
                    status=DocumentStatus.PENDING,
                    size_bytes=1,
                )
                db.add(doc)
                await db.commit()
                made.append(doc.id)
            await ingest_document(factory, made[-1], SMALL_PDF, provider)

        yield {"shared": made[0], "other_doc": made[1], "other_user": other_id, "factory": factory}

        for doc_id in made:
            async with factory() as db:
                doc = await db.get(Document, doc_id)
                if doc:
                    await db.delete(doc)
                    await db.commit()
        async with factory() as db:
            user = await db.get(User, other_id)
            if user:
                await db.delete(user)
                await db.commit()

    async def test_returns_results(self, corpus):
        async with corpus["factory"]() as db:
            hits = await retrieve(db, "ambulance cover", FakeEmbeddings(), user_id=None)
        assert hits

    async def test_never_returns_another_users_chunks(self, corpus):
        """The multi-tenancy guarantee. A leak here exposes one user's uploaded
        policy to everyone, so it is asserted directly rather than inferred."""
        stranger = uuid.uuid4()
        async with corpus["factory"]() as db:
            hits = await retrieve(db, "ambulance cover", FakeEmbeddings(), user_id=stranger)
        assert hits, "the shared corpus should still be reachable"
        assert all(h.document_id != corpus["other_doc"] for h in hits)

    async def test_owner_sees_their_own_document(self, corpus):
        async with corpus["factory"]() as db:
            hits = await retrieve(
                db, "ambulance cover", FakeEmbeddings(), user_id=corpus["other_user"], limit=50
            )
        assert any(h.document_id == corpus["other_doc"] for h in hits)

    async def test_anonymous_sees_only_the_shared_corpus(self, corpus):
        async with corpus["factory"]() as db:
            hits = await retrieve(db, "ambulance cover", FakeEmbeddings(), user_id=None, limit=50)
        assert all(h.document_id == corpus["shared"] for h in hits)

    async def test_sparse_contributes_for_a_natural_language_question(self, corpus):
        """plainto_tsquery ANDs every term, so a full question matched nothing
        and the sparse half was effectively dead. The operators are rewritten to
        OR; this asserts the sparse retriever is actually participating."""
        async with corpus["factory"]() as db:
            hits = await retrieve(
                db, "Can I claim for an ambulance?", FakeEmbeddings(), user_id=None, limit=20
            )
        assert any(h.sparse_rank is not None for h in hits), "sparse retriever returned nothing"

    async def test_fused_score_beats_either_rank_alone(self, corpus):
        async with corpus["factory"]() as db:
            hits = await retrieve(db, "air ambulance cover", FakeEmbeddings(), user_id=None)
        both = [h for h in hits if h.dense_rank and h.sparse_rank]
        one = [h for h in hits if bool(h.dense_rank) ^ bool(h.sparse_rank)]
        if both and one:
            assert max(h.score for h in both) > min(h.score for h in one)

    async def test_results_are_ordered_by_score(self, corpus):
        async with corpus["factory"]() as db:
            hits = await retrieve(db, "ambulance", FakeEmbeddings(), user_id=None, limit=10)
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

    async def test_blank_query_returns_nothing(self, corpus):
        async with corpus["factory"]() as db:
            assert await retrieve(db, "   ", FakeEmbeddings(), user_id=None) == []

    async def test_limit_is_respected(self, corpus):
        async with corpus["factory"]() as db:
            hits = await retrieve(db, "ambulance", FakeEmbeddings(), user_id=None, limit=2)
        assert len(hits) <= 2

    async def test_punctuation_only_query_does_not_error(self, corpus):
        """plainto_tsquery yields an empty query here; the SQL must not blow up."""
        async with corpus["factory"]() as db:
            await retrieve(db, "??? !!!", FakeEmbeddings(), user_id=None)


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------


class TestGrounding:
    def test_citations_are_numbered_from_one(self):
        cits = build_citations([_chunk(), _chunk(), _chunk()])
        assert [c.index for c in cits] == [1, 2, 3]

    def test_citation_carries_provenance(self):
        (cit,) = build_citations([_chunk(filename="bajaj.pdf", page_no=21)])
        assert cit.filename == "bajaj.pdf"
        assert cit.page_no == 21
        assert cit.excerpt

    def test_prompt_numbers_excerpts_to_match_citations(self):
        prompt = build_user_prompt("Is X covered?", [_chunk(body="AAA"), _chunk(body="BBB")])
        assert "[1]" in prompt and "[2]" in prompt
        assert "AAA" in prompt and "BBB" in prompt
        assert "Is X covered?" in prompt

    def test_prompt_includes_page_and_section_for_attribution(self):
        prompt = build_user_prompt("q", [_chunk(page_no=21, section_path="4. Exclusions")])
        assert "page 21" in prompt
        assert "4. Exclusions" in prompt

    def test_weak_retrieval_is_not_treated_as_context(self):
        """Below the floor nothing matched on either retriever, so an answer
        would be invented rather than grounded."""
        assert not has_usable_context([_chunk(score=settings.MIN_RETRIEVAL_SCORE / 10)])

    def test_strong_retrieval_is_usable(self):
        assert has_usable_context([_chunk(score=settings.MIN_RETRIEVAL_SCORE * 10)])

    def test_no_chunks_is_not_usable(self):
        assert not has_usable_context([])

    async def test_refuses_instead_of_guessing_when_nothing_was_found(self):
        """Refusal is the correct answer to a question the policy does not
        address. This must not reach the model at all -- no context, no call."""
        out = [t async for t in stream_answer("What is the capital of France?", [])]
        assert "".join(out) == NO_CONTEXT_ANSWER
