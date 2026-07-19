# SafeShield 🛡️

A RAG-powered assistant for insurance policy documents. Ask questions in plain
English and get answers grounded in your actual policy text, with citations to
the source document, page, and clause.

> **Demo project.** Uploaded documents are processed by a third-party LLM API and
> can be deleted at any time. Not affiliated with any real insurer. Nothing here
> is financial or legal advice.

---

## Status

Under active rebuild. The original version was a Node proxy in front of a
FastAPI service doing pure retrieval — it returned concatenated document
excerpts, not answers. It is being rebuilt as a single FastAPI service with a
real RAG pipeline.

| Phase | Scope | Status |
|---|---|---|
| 1 | FastAPI + Postgres + JWT auth + demo login + rate limiting + CI | ✅ Done |
| 2 | Ingestion: PDF → structure-aware chunks → embeddings → pgvector, in a background worker | ✅ Done (real embeddings pending API quota) |
| 3 | Hybrid retrieval + LLM synthesis with citations, streamed | ⬜ |
| 4 | Evaluation harness + quality regression gate in CI | ⬜ |
| 5 | React frontend | ⬜ |
| 6 | Compose + Prometheus/Grafana + deploy | ⬜ |
| 7 | Kubernetes manifests + Helm + Terraform | ⬜ |

---

## Architecture

```
React (Vercel)
   │ HTTPS + JWT
   ▼
FastAPI ─── OpenAI (embeddings + chat)
   │
   ├── ARQ worker  ── background document ingestion
   │
   ├── Postgres + pgvector  ── users, documents, chunks, embeddings
   └── Redis  ── job queue + rate limiting
```

One backend service. The previous Node layer was a pass-through proxy with no
logic of its own and has been removed.

### Design decisions

**pgvector over a dedicated vector database.** Postgres is already required for
users and documents. Keeping embeddings in the same database means one datastore
to run and back up, and transactional consistency between a document row and its
chunks. A dedicated vector DB wins above ~50M vectors; this project is nowhere
near that.

**Hybrid retrieval, not pure vector search.** Dense embeddings match on meaning
but reliably miss exact strings — ask for "clause 4.2.1" and vector search will
not find clause 4.2.1. Postgres full-text search covers that half, and the two
ranked lists are fused with Reciprocal Rank Fusion.

**Ingestion runs in a background worker.** Embedding a 200-page policy takes
minutes. Doing that inside the HTTP request blocks a worker, times out at the
load balancer, and stalls every other user.

**Refresh tokens are stored server-side.** Pure stateless JWT cannot revoke a
session — a stolen token stays valid until it expires. Short-lived access tokens
are paired with a rotating, revocable refresh token; replaying a rotated token
revokes its entire family.

**psycopg3 over asyncpg.** One driver covers the async application and the
synchronous Alembic migrations under a single URL scheme, and it avoids a
compiled-extension load failure seen on some Windows setups.

**Extraction emits typed blocks, not a flat string.** `page.extract_text()`
interleaves benefit-table cells into surrounding prose, making the numbers
unrecoverable. Tables are extracted separately as markdown and never split
across chunks; headings become their own blocks so every chunk can carry a
section breadcrumb. Running headers and footers are detected by cross-page
repetition and dropped — left in, the insurer's contact details outweigh the
policy text in the index.

**The embedding provider is an interface.** A deterministic fake lets CI run the
full ingestion path — extract, chunk, embed, store, retrieve — with no API key
and no network. Only the vectors differ.

### Extraction results on the sample corpus

| Document | Pages | Chunks | Median tokens |
|---|---:|---:|---:|
| BAJHLIP23020V012223 | 49 | 224 | 132 |
| CHOTGDP23004V012223 | 101 | 295 | 169 |
| ICIHLIP22012V012223 | — | 148 | 162 |
| HDFHLIP23024V072223 | — | 103 | 177 |
| HDFC Poorna Suraksha | 31 | 68 | 278 |
| EDLHLGA23009V012223 | 2 | 7 | 198 |
| **Total** | | **845** | |

100% of chunks carry a section breadcrumb; none exceed the token cap.

---

## Running locally

### With Docker (recommended)

```bash
cp backend/.env.example backend/.env   # then edit JWT_SECRET
docker compose up
```

API on <http://localhost:8000>, interactive docs at `/docs`.

### Without Docker

Requires Python 3.12+ and a PostgreSQL 16+ instance with the `pgvector`
extension.

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"

cp .env.example .env    # point DATABASE_URL at your Postgres
alembic upgrade head
python run.py           # API   (RELOAD=true for auto-reload)
python worker.py        # background ingestion worker, in another shell

python scripts/seed_corpus.py   # index the sample policies
```

Use `run.py` and `worker.py` rather than invoking `uvicorn` or `arq` directly.
Both build their event loop before anything else, because on Windows the default
is a `ProactorEventLoop` and psycopg3 cannot run on it — the worker in particular
would start, accept jobs, and then fail every one at the first query. They are
no-ops on Linux and macOS, so the same commands work everywhere.

A local Postgres already on 5432 will shadow the compose container silently; set
`POSTGRES_PORT` to move it.

Use `run.py` rather than invoking `uvicorn` directly. On Windows, uvicorn builds
its event loop before importing the app, and psycopg3 cannot run on Windows'
default `ProactorEventLoop` — `run.py` selects a compatible loop first. It is a
no-op on Linux and macOS, so the same command works everywhere.

### Tests

Tests run against a real Postgres — mocking the database hides the bugs that
actually happen.

```bash
createdb safeshield_test          # once
cd backend && pytest -v
```

---

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | — | Create an account |
| POST | `/api/auth/login` | — | Access token + refresh cookie |
| POST | `/api/auth/demo` | — | One-click sign-in to the demo account |
| POST | `/api/auth/refresh` | cookie | Rotate tokens |
| POST | `/api/auth/logout` | cookie | Revoke the session family |
| GET | `/api/auth/me` | Bearer | Current user |
| POST | `/api/documents` | Bearer | Upload a PDF → `202`, ingested in background |
| GET | `/api/documents` | Bearer | Own documents + shared corpus |
| GET | `/api/documents/{id}` | Bearer | Poll ingestion status |
| DELETE | `/api/documents/{id}` | Bearer | Delete own document (+ chunks, + file) |
| GET | `/api/health` | — | Liveness |

Access tokens go in the response body and belong in memory on the client.
The refresh token is an httpOnly cookie and is never readable from JavaScript.

---

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI, Pydantic v2, Uvicorn |
| Database | PostgreSQL 17 + pgvector, SQLAlchemy 2 (async), Alembic |
| Queue | Redis + ARQ |
| Auth | Argon2id, PyJWT, rotating refresh tokens |
| RAG | pdfplumber, OpenAI `text-embedding-3-small`, `gpt-4o-mini` |
| Frontend | React, Vite, TypeScript, Tailwind, TanStack Query |
| Quality | pytest, ruff, mypy (strict), GitHub Actions |
| Ops | Docker, Prometheus, Grafana |

---

## License

MIT
