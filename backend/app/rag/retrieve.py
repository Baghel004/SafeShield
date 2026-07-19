"""Hybrid retrieval: dense + sparse, fused with Reciprocal Rank Fusion.

Measured on this corpus, neither method is sufficient alone:

    "What happens if I do not make a claim for a year?"
        dense  -> 4. Submit claim          (wrong)
        sparse -> 5.1 Cumulative Bonus     (right)

    "Can I claim for an ambulance?"
        dense  -> 7. Air Ambulance         (right)
        sparse -> Well Baby Well Mother    (wrong)

Dense handles vocabulary mismatch -- nobody phrases a question using the words
the policy uses. Sparse handles exact strings -- clause numbers, rupee amounts,
and terms of art like "cumulative bonus" that embeddings blur into neighbours.

RRF fuses the two ranked lists without needing score normalisation, which
matters because cosine distance and ts_rank_cd are not comparable quantities.
A document ranked well by both rises above one ranked highly by either alone.

Every query is scoped to the caller: their own chunks plus the shared corpus,
never anyone else's.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.rag.embed import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """A candidate answer fragment, with provenance for citation."""

    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_no: int
    section_path: str
    body: str
    token_count: int
    dense_rank: int | None
    sparse_rank: int | None
    score: float

    @property
    def citation(self) -> str:
        """Human-readable source, shown next to the answer."""
        where = f"p.{self.page_no}"
        if self.section_path:
            where = f"{where}, {self.section_path}"
        return f"{self.filename} ({where})"


# One round trip. Both retrievers run inside the query and are fused in SQL;
# pulling candidates into Python to merge them would triple the latency.
_HYBRID_SQL = text(
    """
WITH dense AS (
    SELECT c.id,
           ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rank
    FROM chunks c
    WHERE (c.user_id = CAST(:uid AS uuid) OR c.user_id IS NULL)
      -- Applied here, not to the results. Filtering after the fact asks for the
      -- global top-k and then throws most of it away, so a scoped question
      -- returns nothing whenever other documents fill the ranking.
      AND (CAST(:doc_id AS uuid) IS NULL OR c.document_id = CAST(:doc_id AS uuid))
      AND c.embedding IS NOT NULL
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :candidates
),
sparse AS (
    SELECT c.id,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.tsv, q.query) DESC) AS rank
    FROM chunks c,
         -- plainto_tsquery ANDs every term, so a natural-language question
         -- ("What happens if I do not make a claim for a year?") becomes
         -- 'happen & make & claim & year' and matches almost nothing. Swapping
         -- the operators to OR turns this back into a real lexical retriever:
         -- any content word can match, and ts_rank_cd ranks by how many and how
         -- rare the matches are. plainto_tsquery still does the stemming,
         -- stop-word removal and escaping, so the input stays safe.
         (SELECT replace(plainto_tsquery('english', :qtext)::text, '&', '|')::tsquery) AS q(query)
    WHERE (c.user_id = CAST(:uid AS uuid) OR c.user_id IS NULL)
      AND (CAST(:doc_id AS uuid) IS NULL OR c.document_id = CAST(:doc_id AS uuid))
      AND q.query IS NOT NULL
      AND c.tsv @@ q.query
    ORDER BY ts_rank_cd(c.tsv, q.query) DESC
    LIMIT :candidates
)
SELECT c.id, c.document_id, d.filename, c.page_no, c.section_path, c.body,
       c.token_count,
       dn.rank AS dense_rank,
       sp.rank AS sparse_rank,
       COALESCE(1.0 / (:rrf_k + dn.rank), 0.0)
     + COALESCE(1.0 / (:rrf_k + sp.rank), 0.0) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
LEFT JOIN dense  dn ON dn.id = c.id
LEFT JOIN sparse sp ON sp.id = c.id
WHERE dn.id IS NOT NULL OR sp.id IS NOT NULL
ORDER BY score DESC, c.token_count DESC
LIMIT :limit
"""
)


async def retrieve(
    db: AsyncSession,
    query: str,
    provider: EmbeddingProvider,
    *,
    user_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    limit: int | None = None,
    candidates: int | None = None,
) -> list[RetrievedChunk]:
    """Return the best chunks for a query, fused across both retrievers.

    `user_id=None` searches only the shared corpus. `document_id` narrows the
    search to a single document; it does not authorise access to it, so callers
    must confirm the document is visible to the user first.
    """
    query = query.strip()
    if not query:
        return []

    limit = limit or settings.RETRIEVAL_TOP_K
    candidates = candidates or settings.RETRIEVAL_CANDIDATES

    vectors = await provider.embed([query])
    if not vectors:
        return []

    rows = (
        await db.execute(
            _HYBRID_SQL,
            {
                "qvec": str(vectors[0]),
                "qtext": query,
                "uid": str(user_id) if user_id else None,
                "doc_id": str(document_id) if document_id else None,
                "candidates": candidates,
                "limit": limit,
                "rrf_k": settings.RRF_K,
            },
        )
    ).mappings()

    results = [
        RetrievedChunk(
            id=r["id"],
            document_id=r["document_id"],
            filename=r["filename"],
            page_no=r["page_no"],
            section_path=r["section_path"] or "",
            body=r["body"],
            token_count=r["token_count"],
            dense_rank=r["dense_rank"],
            sparse_rank=r["sparse_rank"],
            score=float(r["score"]),
        )
        for r in rows
    ]

    logger.debug(
        "retrieved %d chunks for %r (dense-only=%d, sparse-only=%d, both=%d)",
        len(results),
        query[:60],
        sum(1 for c in results if c.dense_rank and not c.sparse_rank),
        sum(1 for c in results if c.sparse_rank and not c.dense_rank),
        sum(1 for c in results if c.dense_rank and c.sparse_rank),
    )
    return results
