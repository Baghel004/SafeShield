"""Structure-aware chunking.

Splits at section boundaries rather than on a fixed character stride, and
prepends each chunk with its heading breadcrumb before embedding.

Why not fixed-width: a 1200-character stride cuts clauses mid-sentence and
strands the numbers in a benefits table from the row labels that give them
meaning. It also drops the heading, so a chunk describing a waiting period is
unfindable unless the body happens to repeat the phrase "waiting period".

Tables are never split. A benefits table sliced in half is worse than useless --
the surviving half looks authoritative while being incomplete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from app.rag.extract import Block

TARGET_TOKENS = 700
MAX_TOKENS = 1100
MIN_TOKENS = 40
OVERLAP_TOKENS = 100

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


@dataclass(frozen=True)
class Chunk:
    """One indexed unit."""

    content: str  # what gets embedded: breadcrumb + body
    body: str  # body alone, for display back to the user
    page_no: int
    section_path: str
    token_count: int


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    # Matches the tokenizer used by the OpenAI embedding models.
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text, disallowed_special=()))


def _truncate_to_tokens(text: str, limit: int) -> str:
    enc = _encoder()
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= limit:
        return text
    return enc.decode(tokens[:limit])


def _tail_tokens(text: str, limit: int) -> str:
    enc = _encoder()
    tokens = enc.encode(text, disallowed_special=())
    if len(tokens) <= limit:
        return text
    return enc.decode(tokens[-limit:])


class _HeadingStack:
    """Tracks the current section breadcrumb as blocks stream past."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def push(self, level: int, text: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, text))

    @property
    def path(self) -> str:
        return " > ".join(text for _, text in self._stack)


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    return parts or ([text] if text.strip() else [])


def _compose(section_path: str, body: str) -> str:
    """Prefix the breadcrumb so the heading is part of what gets embedded."""
    return f"{section_path}\n\n{body}" if section_path else body


class _Accumulator:
    """Collects text until it reaches the target size, then emits a chunk."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.tokens = 0
        self.page: int | None = None

    def add(self, text: str, page_no: int, tokens: int) -> None:
        if self.page is None:
            self.page = page_no
        self.parts.append(text)
        self.tokens += tokens

    @property
    def body(self) -> str:
        return " ".join(self.parts).strip()

    def reset_with_overlap(self, overlap: str) -> None:
        self.parts = [overlap] if overlap else []
        self.tokens = count_tokens(overlap) if overlap else 0
        self.page = None


def chunk_blocks(blocks: list[Block]) -> list[Chunk]:
    """Turn an ordered block stream into embeddable chunks."""
    chunks: list[Chunk] = []
    headings = _HeadingStack()
    acc = _Accumulator()

    def flush() -> None:
        body = acc.body
        if not body:
            acc.reset_with_overlap("")
            return
        tokens = count_tokens(body)
        if tokens < MIN_TOKENS and chunks:
            # Too small to stand alone -- fold it into the previous chunk.
            prev = chunks[-1]
            merged_body = f"{prev.body} {body}".strip()
            merged = _compose(prev.section_path, merged_body)
            if count_tokens(merged) <= MAX_TOKENS:
                chunks[-1] = Chunk(
                    content=merged,
                    body=merged_body,
                    page_no=prev.page_no,
                    section_path=prev.section_path,
                    token_count=count_tokens(merged),
                )
                acc.reset_with_overlap("")
                return

        path = headings.path
        content = _compose(path, body)
        chunks.append(
            Chunk(
                content=content,
                body=body,
                page_no=acc.page or 1,
                section_path=path,
                token_count=count_tokens(content),
            )
        )
        acc.reset_with_overlap(_tail_tokens(body, OVERLAP_TOKENS))

    for block in blocks:
        if block.kind == "heading":
            # A heading starts a new section, so close out the current chunk.
            flush()
            acc.reset_with_overlap("")
            headings.push(block.level, block.text)
            continue

        if block.kind == "table":
            flush()
            acc.reset_with_overlap("")
            path = headings.path
            body = _truncate_to_tokens(block.text, MAX_TOKENS)
            content = _compose(path, body)
            chunks.append(
                Chunk(
                    content=content,
                    body=body,
                    page_no=block.page_no,
                    section_path=path,
                    token_count=count_tokens(content),
                )
            )
            continue

        for sentence in _split_sentences(block.text):
            tokens = count_tokens(sentence)

            # A single sentence longer than the cap has to be hard-split.
            if tokens > MAX_TOKENS:
                flush()
                acc.reset_with_overlap("")
                remaining = sentence
                while remaining:
                    piece = _truncate_to_tokens(remaining, TARGET_TOKENS)
                    acc.add(piece, block.page_no, count_tokens(piece))
                    flush()
                    remaining = remaining[len(piece) :].strip()
                continue

            if acc.tokens + tokens > TARGET_TOKENS and acc.tokens >= MIN_TOKENS:
                flush()
            acc.add(sentence, block.page_no, tokens)

    flush()
    return [c for c in chunks if c.body.strip()]
