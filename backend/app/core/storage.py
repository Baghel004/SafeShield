"""Where uploaded PDFs live.

An interface with two implementations, the same shape as the embedding
provider: local disk for development and tests, object storage for anything
running more than one replica.

The abstraction is not decoration. Writing to a container-local directory works
perfectly on one pod and fails silently on two: an upload handled by replica A
is invisible to replica B, and the ingestion worker is a *third* process that
may not share a filesystem with either. The document row exists, the API
reports it, and ingestion fails with a missing file. That failure only appears
under the horizontal scaling the whole deployment is built for, which is the
worst time to discover it.

Extraction needs a real path on disk -- pdfplumber opens a file, not a stream --
so the read side is a context manager that materialises a temporary copy when
the backend is remote, and hands over the real path when it is not.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class DocumentNotStoredError(RuntimeError):
    """The bytes for a document are not where they should be."""


class DocumentStorage(Protocol):
    """Stores one PDF per document id."""

    def put(self, document_id: uuid.UUID, data: bytes) -> None: ...

    def delete(self, document_id: uuid.UUID) -> None: ...

    @contextmanager
    def as_local_path(self, document_id: uuid.UUID) -> Iterator[Path]:
        """Yield a readable path to the document, for the duration of the block."""
        ...


def _key(document_id: uuid.UUID) -> str:
    """Server-generated. The client never influences the storage location."""
    return f"{document_id}.pdf"


class LocalStorage:
    """Files on the local filesystem.

    Correct for development, tests, and any single-replica deployment with a
    persistent volume. Not correct the moment a second pod exists.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory or settings.UPLOAD_DIR)

    def put(self, document_id: uuid.UUID, data: bytes) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / _key(document_id)).write_bytes(data)

    def delete(self, document_id: uuid.UUID) -> None:
        (self._dir / _key(document_id)).unlink(missing_ok=True)

    @contextmanager
    def as_local_path(self, document_id: uuid.UUID) -> Iterator[Path]:
        path = self._dir / _key(document_id)
        if not path.exists():
            raise DocumentNotStoredError(f"No stored file for document {document_id}")
        # Already local: hand over the real path rather than copying it.
        yield path


class S3Storage:
    """Objects in S3 (or anything speaking its API -- MinIO, R2).

    Credentials are never passed in. On EKS the pod assumes a role through
    IRSA, and boto3's default chain picks it up; locally it falls back to the
    usual environment or profile. A static key in a manifest would be one more
    secret to rotate and leak.
    """

    def __init__(self, bucket: str | None = None, prefix: str | None = None) -> None:
        bucket = bucket or settings.S3_BUCKET
        if not bucket:
            raise ValueError("S3_BUCKET must be set when STORAGE_BACKEND=s3")

        import boto3

        self._bucket = bucket
        self._prefix = (prefix if prefix is not None else settings.S3_PREFIX).strip("/")
        self._client = boto3.client("s3", endpoint_url=settings.S3_ENDPOINT_URL or None)

    def _object_key(self, document_id: uuid.UUID) -> str:
        return f"{self._prefix}/{_key(document_id)}" if self._prefix else _key(document_id)

    def put(self, document_id: uuid.UUID, data: bytes) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._object_key(document_id),
            Body=data,
            ContentType="application/pdf",
        )

    def delete(self, document_id: uuid.UUID) -> None:
        # S3 delete is idempotent and does not error on a missing key, which
        # matches the local implementation's missing_ok=True.
        self._client.delete_object(Bucket=self._bucket, Key=self._object_key(document_id))

    @contextmanager
    def as_local_path(self, document_id: uuid.UUID) -> Iterator[Path]:
        from botocore.exceptions import ClientError

        key = self._object_key(document_id)
        # mkstemp rather than NamedTemporaryFile: what is needed here is a
        # reserved *path*, not an open handle. pdfplumber opens the file itself,
        # and on Windows a NamedTemporaryFile cannot be reopened by a second
        # handle while the first is still open.
        fd, name = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        path = Path(name)
        try:
            try:
                self._client.download_file(self._bucket, key, str(path))
            except ClientError as exc:
                raise DocumentNotStoredError(
                    f"No stored object for document {document_id} (s3://{self._bucket}/{key})"
                ) from exc
            yield path
        finally:
            path.unlink(missing_ok=True)


def get_storage() -> DocumentStorage:
    """Return the configured backend."""
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage()
