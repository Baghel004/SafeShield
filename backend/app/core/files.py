"""Upload validation and safe storage.

Uploads come from strangers, so nothing supplied by the client is trusted:
the filename is discarded for storage purposes, and the content type header is
ignored in favour of inspecting the bytes.
"""

from __future__ import annotations

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


def validate_pdf_bytes(data: bytes, max_bytes: int) -> None:
    """Check size and magic bytes. Raises InvalidUploadError on failure."""
    if not data:
        raise InvalidUploadError("File is empty")
    if len(data) > max_bytes:
        raise InvalidUploadError(
            f"File is {len(data) // 1024 // 1024}MB; the limit is {max_bytes // 1024 // 1024}MB"
        )
    # Trust the bytes, not the Content-Type header or the extension.
    if not data.startswith(_PDF_MAGIC):
        raise InvalidUploadError("File is not a PDF")


def storage_path(upload_dir: str | Path, document_id: uuid.UUID) -> Path:
    """Server-generated path. The client never influences this."""
    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{document_id}.pdf"
