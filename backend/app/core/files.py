"""Upload validation and safe storage.

Uploads come from strangers, so nothing supplied by the client is trusted:
the filename is discarded for storage purposes, and the content type header is
ignored in favour of inspecting the bytes.
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

_PDF_MAGIC = b"%PDF-"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class InvalidUploadError(ValueError):
    """Raised when an upload fails validation."""


def sanitize_filename(filename: str | None) -> str:
    """Reduce a client-supplied filename to a safe display name.

    Only ever used for display. Storage paths are generated server-side, so a
    traversal attempt like `../../etc/passwd` cannot escape anywhere -- but the
    name is still stripped of separators before it reaches a log or a UI.
    """
    name = Path(filename or "document.pdf").name  # drops any directory component
    name = _SAFE_NAME.sub("_", name).strip("._") or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name[:255]


def validate_pdf_bytes(data: bytes, max_bytes: int, max_pages: int | None = None) -> None:
    """Check size, magic bytes and page count.

    Raises InvalidUploadError on failure.
    """
    if not data:
        raise InvalidUploadError("File is empty")
    if len(data) > max_bytes:
        raise InvalidUploadError(
            f"File is {len(data) // 1024 // 1024}MB; the limit is {max_bytes // 1024 // 1024}MB"
        )
    # Trust the bytes, not the Content-Type header or the extension.
    if not data.startswith(_PDF_MAGIC):
        raise InvalidUploadError("File is not a PDF")

    if max_pages is not None:
        _check_page_count(data, max_pages)


def _check_page_count(data: bytes, max_pages: int) -> None:
    """Reject documents with more pages than we are willing to embed.

    Size alone is a poor proxy for cost: PDFs compress well, so a text-only
    3000-page document sits comfortably under a 25MB cap while costing far more
    to embed than a short scanned one. The page count is what actually predicts
    the bill and the ingestion time, so it is what gets checked.

    Done here rather than in the worker so the caller is told immediately, and
    the file is never written to disk.
    """
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
    except InvalidUploadError:
        raise
    except Exception as exc:
        # A file that starts with %PDF- but cannot be opened is corrupt or
        # encrypted. Better to reject it now than to accept it, queue a job and
        # fail asynchronously where the user has to poll to discover it.
        raise InvalidUploadError("File could not be read as a PDF") from exc

    if pages > max_pages:
        raise InvalidUploadError(f"PDF has {pages} pages; the limit is {max_pages}")


def storage_path(upload_dir: str | Path, document_id: uuid.UUID) -> Path:
    """Server-generated path. The client never influences this."""
    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{document_id}.pdf"
