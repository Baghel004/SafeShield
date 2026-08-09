"""Document storage backends.

The interface exists because writing uploads to a container-local directory
works perfectly on one replica and fails silently on two. These assert the two
implementations behave the same way, since the whole point is that ingestion
cannot tell them apart.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.storage import (
    DocumentNotStoredError,
    LocalStorage,
    S3Storage,
    get_storage,
)

PDF = b"%PDF-1.4\nnot a real pdf but the bytes round-trip\n%%EOF"


class TestLocalStorage:
    def test_round_trips_bytes(self, tmp_path: Path):
        storage = LocalStorage(tmp_path)
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)
        with storage.as_local_path(doc_id) as path:
            assert path.read_bytes() == PDF

    def test_creates_the_directory(self, tmp_path: Path):
        storage = LocalStorage(tmp_path / "nested" / "deeper")
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)
        with storage.as_local_path(doc_id) as path:
            assert path.exists()

    def test_path_is_derived_from_the_id_alone(self, tmp_path: Path):
        """The client never influences where a file lands, so a traversal
        attempt in a filename cannot escape the upload directory."""
        doc_id = uuid.uuid4()
        LocalStorage(tmp_path).put(doc_id, PDF)
        assert (tmp_path / f"{doc_id}.pdf").exists()

    def test_missing_document_raises(self, tmp_path: Path):
        with (
            pytest.raises(DocumentNotStoredError),
            LocalStorage(tmp_path).as_local_path(uuid.uuid4()),
        ):
            pass

    def test_delete_is_idempotent(self, tmp_path: Path):
        """Matches S3, where deleting a missing key is not an error. A backend
        that raised here would break the delete endpoint on retry."""
        storage = LocalStorage(tmp_path)
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)
        storage.delete(doc_id)
        storage.delete(doc_id)

    def test_does_not_copy_a_local_file(self, tmp_path: Path):
        """as_local_path hands over the real path when the backend is already
        local -- copying a 25MB upload per ingestion would be pure waste."""
        storage = LocalStorage(tmp_path)
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)
        with storage.as_local_path(doc_id) as path:
            assert path.parent == tmp_path

    def test_file_survives_the_context(self, tmp_path: Path):
        """The opposite of S3: nothing is cleaned up, because nothing was
        temporary."""
        storage = LocalStorage(tmp_path)
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)
        with storage.as_local_path(doc_id) as path:
            kept = path
        assert kept.exists()


class TestS3Storage:
    @pytest.fixture
    def s3(self):
        client = MagicMock()
        with patch("boto3.client", return_value=client):
            yield S3Storage(bucket="test-bucket", prefix="documents"), client

    def test_requires_a_bucket(self):
        with patch("app.core.storage.settings") as cfg:
            cfg.S3_BUCKET = None
            with pytest.raises(ValueError, match="S3_BUCKET"):
                S3Storage()

    def test_put_uses_a_server_generated_key(self, s3):
        storage, client = s3
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)

        kwargs = client.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "test-bucket"
        assert kwargs["Key"] == f"documents/{doc_id}.pdf"
        assert kwargs["Body"] == PDF
        assert kwargs["ContentType"] == "application/pdf"

    def test_no_credentials_are_passed(self, s3):
        """On EKS the pod assumes a role through IRSA and boto3's default chain
        finds it. A static key in a manifest is one more secret to leak."""
        _, client = s3
        with patch("boto3.client", return_value=client) as factory:
            S3Storage(bucket="b")
        assert "aws_access_key_id" not in factory.call_args.kwargs

    def test_downloads_to_a_temporary_file(self, s3):
        storage, client = s3
        doc_id = uuid.uuid4()

        def fake_download(bucket: str, key: str, dest: str) -> None:
            Path(dest).write_bytes(PDF)

        client.download_file.side_effect = fake_download

        with storage.as_local_path(doc_id) as path:
            assert path.read_bytes() == PDF
            inside = path

        # Cleaned up on exit: ingestion runs continuously and a leaked copy of
        # every uploaded PDF fills the disk.
        assert not inside.exists()

    def test_temporary_file_is_removed_even_on_failure(self, s3):
        storage, client = s3
        client.download_file.side_effect = lambda b, k, d: Path(d).write_bytes(PDF)

        leaked: Path | None = None
        with (
            pytest.raises(RuntimeError, match="boom"),
            storage.as_local_path(uuid.uuid4()) as path,
        ):
            leaked = path
            raise RuntimeError("boom")

        assert leaked is not None
        assert not leaked.exists()

    def test_missing_object_raises_the_same_error_as_local(self, s3):
        from botocore.exceptions import ClientError

        storage, client = s3
        client.download_file.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject"
        )

        with pytest.raises(DocumentNotStoredError), storage.as_local_path(uuid.uuid4()):
            pass

    def test_delete_removes_the_object(self, s3):
        storage, client = s3
        doc_id = uuid.uuid4()
        storage.delete(doc_id)
        assert client.delete_object.call_args.kwargs["Key"] == f"documents/{doc_id}.pdf"

    def test_empty_prefix_produces_a_bare_key(self):
        client = MagicMock()
        with patch("boto3.client", return_value=client):
            storage = S3Storage(bucket="b", prefix="")
        doc_id = uuid.uuid4()
        storage.put(doc_id, PDF)
        assert client.put_object.call_args.kwargs["Key"] == f"{doc_id}.pdf"


class TestBackendSelection:
    def test_defaults_to_local(self):
        assert isinstance(get_storage(), LocalStorage)

    def test_selects_s3_when_configured(self):
        with patch("app.core.storage.settings") as cfg:
            cfg.STORAGE_BACKEND = "s3"
            cfg.S3_BUCKET = "bucket"
            cfg.S3_PREFIX = "documents"
            cfg.S3_ENDPOINT_URL = None
            with patch("boto3.client", return_value=MagicMock()):
                assert isinstance(get_storage(), S3Storage)
