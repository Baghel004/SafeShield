"""Document upload/list/delete API tests.

Ingestion is not exercised here -- the endpoint enqueues and returns 202, so
these cover the request surface and, importantly, the ownership boundary.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


async def _auth(client: AsyncClient, email: str = "owner@example.com") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": "correct-horse-battery"}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _upload(name: str = "policy.pdf", data: bytes = MINIMAL_PDF) -> dict:
    return {"file": (name, data, "application/pdf")}


@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep test uploads out of the real upload directory."""
    from app.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path), raising=False)


class TestUpload:
    async def test_requires_authentication(self, client: AsyncClient):
        resp = await client.post("/api/documents", files=_upload())
        assert resp.status_code == 401

    async def test_accepts_a_pdf_and_returns_202(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post("/api/documents", files=_upload(), headers=headers)
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["filename"] == "policy.pdf"
        assert body["is_shared"] is False

    async def test_rejects_non_pdf_content(self, client: AsyncClient):
        """Validation is on the bytes, so a .pdf name does not get it through."""
        headers = await _auth(client)
        resp = await client.post(
            "/api/documents", files=_upload(data=b"<?php system($_GET[0]); ?>"), headers=headers
        )
        assert resp.status_code == 422
        assert "not a PDF" in resp.json()["detail"]

    async def test_rejects_empty_file(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post("/api/documents", files=_upload(data=b""), headers=headers)
        assert resp.status_code == 422

    async def test_sanitizes_traversal_in_filename(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.post(
            "/api/documents", files=_upload(name="../../../etc/passwd.pdf"), headers=headers
        )
        assert resp.status_code == 202
        stored = resp.json()["filename"]
        assert "/" not in stored and ".." not in stored

    async def test_writes_the_file_under_a_server_generated_name(
        self, client: AsyncClient, tmp_path: Path
    ):
        headers = await _auth(client)
        resp = await client.post("/api/documents", files=_upload(), headers=headers)
        doc_id = resp.json()["id"]
        assert (tmp_path / f"{doc_id}.pdf").exists()


class TestListAndGet:
    async def test_lists_own_documents(self, client: AsyncClient):
        headers = await _auth(client)
        await client.post("/api/documents", files=_upload("a.pdf"), headers=headers)
        await client.post("/api/documents", files=_upload("b.pdf"), headers=headers)

        resp = await client.get("/api/documents", headers=headers)
        assert resp.status_code == 200
        assert {d["filename"] for d in resp.json()["documents"]} == {"a.pdf", "b.pdf"}

    async def test_includes_the_shared_corpus(self, client: AsyncClient, db: AsyncSession):
        shared = Document(
            user_id=None, filename="sample.pdf", status=DocumentStatus.READY, size_bytes=1
        )
        db.add(shared)
        await db.flush()

        headers = await _auth(client)
        resp = await client.get("/api/documents", headers=headers)
        entries = {d["filename"]: d for d in resp.json()["documents"]}
        assert entries["sample.pdf"]["is_shared"] is True

    async def test_get_returns_status(self, client: AsyncClient):
        headers = await _auth(client)
        doc_id = (await client.post("/api/documents", files=_upload(), headers=headers)).json()[
            "id"
        ]

        resp = await client.get(f"/api/documents/{doc_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] in {"pending", "processing", "ready", "failed"}

    async def test_unknown_id_is_404(self, client: AsyncClient):
        headers = await _auth(client)
        resp = await client.get(f"/api/documents/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404


class TestOwnershipBoundary:
    async def test_cannot_read_another_users_document(self, client: AsyncClient):
        """The core multi-tenancy guarantee: one user's upload is invisible to
        another. Returns 404 rather than 403 -- a 403 confirms the id exists."""
        alice = await _auth(client, "alice@example.com")
        doc_id = (
            await client.post("/api/documents", files=_upload("alice.pdf"), headers=alice)
        ).json()["id"]

        bob = await _auth(client, "bob@example.com")
        assert (await client.get(f"/api/documents/{doc_id}", headers=bob)).status_code == 404

    async def test_another_users_document_is_absent_from_the_list(self, client: AsyncClient):
        alice = await _auth(client, "alice2@example.com")
        await client.post("/api/documents", files=_upload("secret.pdf"), headers=alice)

        bob = await _auth(client, "bob2@example.com")
        listed = (await client.get("/api/documents", headers=bob)).json()["documents"]
        assert "secret.pdf" not in {d["filename"] for d in listed}

    async def test_cannot_delete_another_users_document(self, client: AsyncClient):
        alice = await _auth(client, "alice3@example.com")
        doc_id = (await client.post("/api/documents", files=_upload(), headers=alice)).json()["id"]

        bob = await _auth(client, "bob3@example.com")
        assert (await client.delete(f"/api/documents/{doc_id}", headers=bob)).status_code == 404


class TestDelete:
    async def test_deletes_own_document(self, client: AsyncClient):
        headers = await _auth(client)
        doc_id = (await client.post("/api/documents", files=_upload(), headers=headers)).json()[
            "id"
        ]

        assert (await client.delete(f"/api/documents/{doc_id}", headers=headers)).status_code == 204
        assert (await client.get(f"/api/documents/{doc_id}", headers=headers)).status_code == 404

    async def test_removes_the_stored_file(self, client: AsyncClient, tmp_path: Path):
        headers = await _auth(client)
        doc_id = (await client.post("/api/documents", files=_upload(), headers=headers)).json()[
            "id"
        ]
        assert (tmp_path / f"{doc_id}.pdf").exists()

        await client.delete(f"/api/documents/{doc_id}", headers=headers)
        assert not (tmp_path / f"{doc_id}.pdf").exists()

    async def test_shared_documents_cannot_be_deleted(self, client: AsyncClient, db: AsyncSession):
        shared = Document(
            user_id=None, filename="shared.pdf", status=DocumentStatus.READY, size_bytes=1
        )
        db.add(shared)
        await db.flush()

        headers = await _auth(client)
        resp = await client.delete(f"/api/documents/{shared.id}", headers=headers)
        assert resp.status_code == 403
