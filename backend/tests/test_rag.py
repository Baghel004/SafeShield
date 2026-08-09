"""Extraction, chunking, embedding and upload-validation tests.

The extraction and chunking tests run against the real policy PDFs in
`datasets/`. Synthetic fixtures would not exercise what actually makes these
documents hard: repeated page furniture, benefit tables, and numbered clauses.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from pathlib import Path

import pytest

from app.core.files import InvalidUploadError, sanitize_filename, storage_path, validate_pdf_bytes
from app.rag.chunk import MAX_TOKENS, Chunk, chunk_blocks, count_tokens
from app.rag.embed import FakeEmbeddings
from app.rag.extract import _PAGE_MARKER, Block, _boilerplate_template, extract_blocks

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
SAMPLE_PDF = DATASETS / "BAJHLIP23020V012223.pdf"

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _has_datasets() -> bool:
    return SAMPLE_PDF.exists()


needs_datasets = pytest.mark.skipif(not _has_datasets(), reason="sample PDFs not present")


@pytest.fixture(scope="module")
def sample_blocks() -> list[Block]:
    return extract_blocks(SAMPLE_PDF)


@pytest.fixture(scope="module")
def sample_chunks(sample_blocks: list[Block]) -> list[Chunk]:
    return chunk_blocks(sample_blocks)


@needs_datasets
class TestExtraction:
    def test_produces_all_three_block_kinds(self, sample_blocks: list[Block]):
        kinds = {b.kind for b in sample_blocks}
        assert {"heading", "text", "table"} <= kinds

    def test_finds_numbered_clause_headings(self, sample_blocks: list[Block]):
        headings = [b.text for b in sample_blocks if b.kind == "heading"]
        assert any(h.startswith("1.") for h in headings)
        assert any("SECTION" in h.upper() for h in headings)

    def test_tables_render_as_markdown(self, sample_blocks: list[Block]):
        tables = [b for b in sample_blocks if b.kind == "table"]
        assert tables
        assert all(t.text.startswith("|") for t in tables)
        assert all("---" in t.text for t in tables)

    def test_strips_running_headers_and_footers(self, sample_blocks: list[Block]):
        """Page furniture repeats on every page; left in, it dominates the corpus.

        Asserts the property rather than a specific string: the insurer's domain
        also appears inside a genuine grievance-redressal clause, so matching on
        it would fail on correct output.
        """
        pages = max(b.page_no for b in sample_blocks)
        counts = Counter(b.text for b in sample_blocks if b.kind != "table")
        line, occurrences = counts.most_common(1)[0]
        assert occurrences < pages * 0.3, (
            f"{line[:60]!r} survived on {occurrences} of {pages} pages"
        )

    def test_known_page_furniture_is_gone(self, sample_blocks: list[Block]):
        assert "Issuing Office" not in " ".join(b.text for b in sample_blocks)

    def test_page_number_footers_are_dropped(self, sample_blocks: list[Block]):
        """Footers carry the page number, so they differ on every page and exact
        matching never sees a repeat. They previously survived as whole chunks
        and polluted retrieval -- a search for "maternity waiting period"
        returned "33 | P age" as a top hit."""
        pagey = re.compile(r"p\s*a\s*g\s*e", re.IGNORECASE)
        leaks = [b.text for b in sample_blocks if len(b.text) < 60 and pagey.search(b.text)]
        assert not leaks, f"page markers survived: {leaks[:3]}"

    @pytest.mark.parametrize(
        ("line", "is_marker"),
        [
            ("33 | P age", True),  # letter-spacing splits "Page"
            ("34 | P a g e", True),
            ("Page 12", True),
            ("Page 3 of 49", True),
            ("12", True),
            ("4.2 Waiting Periods", False),
            ("Section 12 applies", False),
            ("2. Any one Illness :-", False),
        ],
    )
    def test_page_marker_pattern(self, line: str, is_marker: bool):
        assert bool(_PAGE_MARKER.match(line)) is is_marker

    def test_boilerplate_template_normalizes_digits(self):
        assert _boilerplate_template("33 | P age") == _boilerplate_template("34 | P age")
        assert _boilerplate_template("4. Accident") != _boilerplate_template("Cataract cover")

    def test_normalizes_private_use_glyphs(self, sample_blocks: list[Block]):
        """Symbol-font bullets arrive as PUA codepoints that carry no signal."""
        assert not any("" <= ch <= "" for b in sample_blocks for ch in b.text), (
            "Private Use Area characters leaked into extracted text"
        )

    def test_folds_typographic_quotes_to_ascii(self, sample_blocks: list[Block]):
        text = " ".join(b.text for b in sample_blocks)
        assert "’" not in text and "“" not in text

    def test_page_numbers_are_sane(self, sample_blocks: list[Block]):
        pages = [b.page_no for b in sample_blocks]
        assert min(pages) >= 1
        assert pages == sorted(pages), "blocks must stay in document order"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            extract_blocks(DATASETS / "does-not-exist.pdf")


@needs_datasets
class TestChunking:
    def test_produces_chunks(self, sample_chunks: list[Chunk]):
        assert len(sample_chunks) > 50

    def test_no_chunk_exceeds_the_token_cap(self, sample_chunks: list[Chunk]):
        oversized = [c for c in sample_chunks if c.token_count > MAX_TOKENS]
        assert not oversized, f"{len(oversized)} chunks over {MAX_TOKENS} tokens"

    def test_every_chunk_carries_a_section_breadcrumb(self, sample_chunks: list[Chunk]):
        """The breadcrumb is what makes a clause findable when the body never
        repeats the heading's wording."""
        without = [c for c in sample_chunks if not c.section_path]
        assert len(without) / len(sample_chunks) < 0.05

    def test_breadcrumb_is_embedded_in_content(self, sample_chunks: list[Chunk]):
        c = next(c for c in sample_chunks if c.section_path)
        assert c.section_path in c.content
        assert c.section_path not in c.body

    def test_tables_are_never_split(self, sample_chunks: list[Chunk]):
        """Half a benefits table looks authoritative while being wrong."""
        for c in sample_chunks:
            if c.body.lstrip().startswith("|"):
                lines = [ln for ln in c.body.splitlines() if ln.strip()]
                assert lines[1].startswith("|---") or "---" in lines[1]

    def test_chunks_are_non_empty(self, sample_chunks: list[Chunk]):
        assert all(c.body.strip() for c in sample_chunks)

    def test_empty_input_yields_no_chunks(self):
        assert chunk_blocks([]) == []

    def test_oversized_single_block_is_split(self):
        huge = Block(kind="text", text="word " * 6000, page_no=1)
        chunks = chunk_blocks([huge])
        assert len(chunks) > 1
        assert all(c.token_count <= MAX_TOKENS for c in chunks)

    def test_heading_starts_a_new_section(self):
        blocks = [
            Block(kind="heading", text="4. Exclusions", page_no=1, level=1),
            Block(kind="text", text="Cosmetic surgery is not covered. " * 12, page_no=1),
            Block(kind="heading", text="5. Claims", page_no=2, level=1),
            Block(kind="text", text="Claims must be filed within 30 days. " * 12, page_no=2),
        ]
        chunks = chunk_blocks(blocks)
        paths = {c.section_path for c in chunks}
        assert "4. Exclusions" in paths
        assert "5. Claims" in paths

    def test_nested_headings_build_a_path(self):
        blocks = [
            Block(kind="heading", text="4. Exclusions", page_no=1, level=1),
            Block(kind="heading", text="4.2 Waiting Periods", page_no=1, level=2),
            Block(kind="text", text="Maternity has a nine month waiting period. " * 10, page_no=1),
        ]
        chunks = chunk_blocks(blocks)
        assert any(c.section_path == "4. Exclusions > 4.2 Waiting Periods" for c in chunks)


class TestTokenCounting:
    def test_counts_tokens(self):
        assert count_tokens("hello world") == 2

    def test_empty_string(self):
        assert count_tokens("") == 0


class TestFakeEmbeddings:
    async def test_returns_one_vector_per_input(self):
        vecs = await FakeEmbeddings(dimensions=64).embed(["a", "b", "c"])
        assert len(vecs) == 3
        assert all(len(v) == 64 for v in vecs)

    async def test_is_deterministic(self):
        p = FakeEmbeddings(dimensions=64)
        assert await p.embed(["same text"]) == await p.embed(["same text"])

    async def test_different_text_differs(self):
        p = FakeEmbeddings(dimensions=64)
        (a,), (b,) = await p.embed(["alpha"]), await p.embed(["beta"])
        assert a != b

    async def test_vectors_are_unit_norm(self):
        (v,) = await FakeEmbeddings(dimensions=128).embed(["policy"])
        assert abs(sum(x * x for x in v) ** 0.5 - 1.0) < 1e-9

    async def test_empty_input(self):
        assert await FakeEmbeddings(dimensions=8).embed([]) == []


class TestUploadValidation:
    def test_accepts_a_pdf(self):
        validate_pdf_bytes(b"%PDF-1.7\n...", max_bytes=1000)

    def test_rejects_empty(self):
        with pytest.raises(InvalidUploadError, match="empty"):
            validate_pdf_bytes(b"", max_bytes=1000)

    def test_rejects_non_pdf_regardless_of_extension(self):
        """The magic bytes decide, not the filename or Content-Type."""
        with pytest.raises(InvalidUploadError, match="not a PDF"):
            validate_pdf_bytes(b"<?php system($_GET[0]); ?>", max_bytes=1000)

    def test_rejects_oversized(self):
        with pytest.raises(InvalidUploadError, match="limit"):
            validate_pdf_bytes(b"%PDF-" + b"x" * 5000, max_bytes=1000)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("policy.pdf", "policy.pdf"),
            ("../../../etc/passwd", "etcpasswd.pdf"),
            ("..\\..\\windows\\system32\\cfg", "windowssystem32cfg.pdf"),
            ("my policy (2024).pdf", "my_policy_2024_.pdf"),
            ("", "document.pdf"),
            (None, "document.pdf"),
        ],
    )
    def test_sanitize_filename(self, raw: str | None, expected: str):
        result = sanitize_filename(raw)
        assert "/" not in result and "\\" not in result and ".." not in result
        assert result.endswith(".pdf")

    def test_storage_path_ignores_client_input(self, tmp_path: Path):
        doc_id = uuid.uuid4()
        path = storage_path(tmp_path, doc_id)
        assert path.parent == tmp_path
        assert path.name == f"{doc_id}.pdf"
