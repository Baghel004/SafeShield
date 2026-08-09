"""PDF extraction.

Produces an ordered stream of typed blocks (headings, paragraphs, tables) rather
than a flat string. Three reasons:

- Insurance policies are heavily tabular. `page.extract_text()` interleaves table
  cells into surrounding prose and the numbers become unrecoverable, so tables
  are pulled out separately and rendered as markdown.
- Headings carry the meaning of the clause beneath them. Keeping them as their
  own blocks lets the chunker attach a section breadcrumb to every chunk, so a
  clause about waiting periods stays findable even when the chunk body never
  repeats the phrase.
- Running headers and footers repeat on every page. Left in, they dominate the
  corpus and pull retrieval toward the insurer's contact details.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pdfplumber

logger = logging.getLogger(__name__)

BlockKind = Literal["heading", "text", "table"]

# "4.", "4.2", "4.2.1", "SECTION 4", "Clause 4.2" -- the numbering styles these
# policy documents actually use.
_NUMBERED_HEADING = re.compile(
    r"^\s*(?:(?:section|clause|article|part)\s+)?(\d+(?:\.\d+)*)\.?\s+(\S.*)$",
    re.IGNORECASE,
)
# "SECTION A) PREAMBLE", "PART B - BENEFITS"
_LETTERED_HEADING = re.compile(
    r"^\s*(?:section|clause|article|part)\s+[A-Z][)\.\-:]?\s*(.*)$", re.IGNORECASE
)
_ALL_CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,&()/'\-]{3,80}$")

# Lines that are contact details or legalese furniture, never section headings.
_NOISE = re.compile(
    r"(www\.|https?://|@|toll\s*free|call at|e-?mail|reg\.?\s*no|cin\b|uin[-\s]|irdai|"
    r"^page\s+\d+|\bltd\.?$|\blimited$)",
    re.IGNORECASE,
)

# Standalone page markers, which survive template matching on short documents.
# "P age" rather than "Page" is not a typo: letter-spacing makes extract_words
# split the word, and the same happens to "P a g e" in some of these PDFs.
_PAGE_MARKER = re.compile(
    r"^\s*(?:page\s*)?\d+\s*(?:\||of|/|-)?\s*(?:p\s*a\s*g\s*e|page)?\s*\d*\s*$",
    re.IGNORECASE,
)

_MAX_HEADING_CHARS = 120
_MAX_BOLD_HEADING_CHARS = 70
_MAX_HEADING_WORDS = 14

# A line repeating on at least this share of pages is running boilerplate.
_BOILERPLATE_PAGE_RATIO = 0.30
_MIN_PAGES_FOR_BOILERPLATE = 4
# How many lines at the top and bottom of a page count as header/footer region.
_MARGIN_LINES = 5


@dataclass(frozen=True)
class Block:
    """A single extracted element, in document order."""

    kind: BlockKind
    text: str
    page_no: int
    level: int = 0  # heading depth; 0 for non-headings


@dataclass(frozen=True)
class _Line:
    text: str
    size: float
    bold: bool
    top: float


# Typographic characters are folded to ASCII so that a keyword search for
# "policyholder's" (straight apostrophe, as typed) matches a document that used
# a curly one. Bullets drawn from Symbol/Wingdings arrive as Private Use Area
# codepoints (U+F0B7 and friends) which carry no meaning to an embedding model
# or a tokenizer, so they are mapped to a real bullet.
_CHAR_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        "�": "",  # replacement char: unmappable glyph, no signal to recover
        "": "•",
        "": "•",
        "": "•",
        "": " ",
    }
)


def _normalize_chars(text: str) -> str:
    folded = text.translate(_CHAR_FOLD)
    # Anything else left in the Private Use Area is an unresolvable symbol glyph.
    return "".join(" " if "" <= ch <= "" else ch for ch in folded)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _normalize_chars(text)).strip()


def _table_to_markdown(table: list[list[str | None]]) -> str:
    """Render a table as markdown so cell relationships survive chunking."""
    rows = [[_clean(cell or "") for cell in row] for row in table if row]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    header, *body = rows
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _build_line(words: list[dict[str, Any]], top: float) -> _Line:
    text = _clean(" ".join(str(w.get("text", "")) for w in words))
    sizes = [float(w.get("size", 0.0)) for w in words if w.get("size")]
    fonts = [str(w.get("fontname", "")) for w in words]
    return _Line(
        text=text,
        size=max(sizes) if sizes else 0.0,
        bold=any("bold" in f.lower() or "black" in f.lower() for f in fonts),
        top=top,
    )


def _words_to_lines(words: list[dict[str, Any]]) -> list[_Line]:
    """Group words into visual lines using their vertical position."""
    if not words:
        return []

    lines: list[_Line] = []
    current: list[dict[str, Any]] = []
    current_top: float | None = None

    for word in sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"]))):
        top = float(word["top"])
        if current_top is None:
            current, current_top = [word], top
        elif abs(top - current_top) <= 2.5:
            current.append(word)
        else:
            lines.append(_build_line(current, current_top))
            current, current_top = [word], top

    if current and current_top is not None:
        lines.append(_build_line(current, current_top))

    return [ln for ln in lines if ln.text]


def _heading_level(line: _Line, body_size: float) -> int:
    """Return a heading depth, or 0 if the line is body text.

    Numbering is the strongest signal in these documents and is checked first.
    Font weight alone is deliberately NOT sufficient: policy PDFs bold plenty of
    inline prose, and accepting it classified over half the document as headings.
    """
    text = line.text
    if not text or len(text) > _MAX_HEADING_CHARS or _NOISE.search(text):
        return 0
    if len(text.split()) > _MAX_HEADING_WORDS:
        return 0

    if match := _NUMBERED_HEADING.match(text):
        return min(match.group(1).count(".") + 1, 6)
    if _LETTERED_HEADING.match(text):
        return 1
    if body_size > 0 and line.size >= body_size * 1.15:
        return 1
    if _ALL_CAPS_HEADING.match(text):
        return 2
    if line.bold and len(text) <= _MAX_BOLD_HEADING_CHARS and not text.endswith("."):
        return 3
    return 0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _boilerplate_template(text: str) -> str:
    """Collapse a line to a shape for cross-page comparison.

    Page furniture usually carries the page number, so the literal text differs
    on every page ("33 | P age", "34 | P age") and exact matching never sees a
    repeat. Normalising digit runs to '#' makes the repetition visible.
    """
    return re.sub(r"\d+", "#", text).strip().lower()


def _detect_boilerplate(pages: list[list[_Line]]) -> set[str]:
    """Identify running headers and footers by cross-page repetition.

    Returns a set of normalised templates, not literal lines. Only the top and
    bottom few lines of each page are considered, so a genuine heading that
    happens to recur mid-page is not discarded.
    """
    if len(pages) < _MIN_PAGES_FOR_BOILERPLATE:
        return set()

    counts: Counter[str] = Counter()
    for lines in pages:
        margin = {ln.text for ln in lines[:_MARGIN_LINES]} | {
            ln.text for ln in lines[-_MARGIN_LINES:]
        }
        counts.update(_boilerplate_template(t) for t in margin)

    threshold = max(2, int(len(pages) * _BOILERPLATE_PAGE_RATIO))
    return {tpl for tpl, n in counts.items() if n >= threshold and tpl}


def extract_blocks(pdf_path: str | Path) -> list[Block]:
    """Extract an ordered list of blocks from a PDF.

    Raises FileNotFoundError if the path does not exist; malformed individual
    pages are logged and skipped rather than failing the whole document.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(path)

    page_lines: list[list[_Line]] = []
    page_tables: list[list[str]] = []

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            try:
                lines, tables = _read_page(page)
            except Exception:  # noqa: BLE001 -- one bad page must not lose the document
                logger.warning("Skipping unreadable page %d of %s", page_no, path.name)
                lines, tables = [], []
            page_lines.append(lines)
            page_tables.append(tables)

    boilerplate = _detect_boilerplate(page_lines)
    if boilerplate:
        logger.debug("Dropping %d boilerplate lines from %s", len(boilerplate), path.name)

    blocks: list[Block] = []
    for idx, (lines, tables) in enumerate(zip(page_lines, page_tables, strict=True)):
        page_no = idx + 1
        blocks.extend(Block(kind="table", text=t, page_no=page_no) for t in tables)
        blocks.extend(_lines_to_blocks(lines, page_no, boilerplate))

    return blocks


def _read_page(page: Any) -> tuple[list[_Line], list[str]]:
    tables = page.find_tables()
    bboxes = [t.bbox for t in tables]

    markdown = [md for t in tables if (md := _table_to_markdown(t.extract()))]

    # Text inside a table's bounding box is already captured by the table above.
    words = [
        w
        for w in page.extract_words(extra_attrs=["size", "fontname"])
        if not _inside_any(w, bboxes)
    ]
    return _words_to_lines(words), markdown


def _lines_to_blocks(lines: list[_Line], page_no: int, boilerplate: set[str]) -> list[Block]:
    kept = [
        ln
        for ln in lines
        if _boilerplate_template(ln.text) not in boilerplate and not _PAGE_MARKER.match(ln.text)
    ]
    if not kept:
        return []

    body_size = _median([ln.size for ln in kept if ln.size])

    out: list[Block] = []
    buffer: list[str] = []
    for line in kept:
        level = _heading_level(line, body_size)
        if level:
            if buffer:
                out.append(Block(kind="text", text=" ".join(buffer), page_no=page_no))
                buffer = []
            out.append(Block(kind="heading", text=line.text, page_no=page_no, level=level))
        else:
            buffer.append(line.text)

    if buffer:
        out.append(Block(kind="text", text=" ".join(buffer), page_no=page_no))

    return out


def _inside_any(word: dict[str, Any], bboxes: list[tuple[float, float, float, float]]) -> bool:
    cx = (float(word["x0"]) + float(word["x1"])) / 2
    cy = (float(word["top"]) + float(word["bottom"])) / 2
    return any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in bboxes)
