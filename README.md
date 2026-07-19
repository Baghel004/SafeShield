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
| 2 | Ingestion: PDF → structure-aware chunks → embeddings → pgvector, in a background worker | ✅ Done |
| 3 | Hybrid retrieval + LLM synthesis with citations, streamed | ✅ Done |
| 4 | Evaluation harness + quality regression gate in CI | ✅ Done |
| 4.5 | Hardening: the gaps an audit of phases 1–4 turned up | ✅ Done |
| 5 | React frontend | ✅ Done |
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

**One backend service.** The original project ran two: a Node/Express proxy and
a separate FastAPI service holding a FAISS index in memory. Both are gone. The
Node layer was a pass-through with no logic of its own, and the FAISS service
has been replaced piece by piece — its extraction, chunking and embedding now
live in `backend/app/rag/`, and its in-process index is a pgvector table.

```
backend/     the FastAPI service — the only backend
frontend/    React client -- auth, uploads, streamed answers with citations
datasets/    six public insurer policies used as the shared corpus
scripts/     database bootstrap
```

The in-memory FAISS index was also the reason the old service could not scale:
every replica would have held a different index, so an upload indexed by one pod
was invisible to the next request. Moving the vectors into Postgres is what makes
horizontal scaling possible at all.

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
| BAJHLIP23020V012223 | 49 | 222 | 132 |
| CHOTGDP23004V012223 | 101 | 294 | 169 |
| ICIHLIP22012V012223 | 31 | 147 | 162 |
| HDFHLIP23024V072223 | 39 | 100 | 177 |
| HDFC Poorna Suraksha | 31 | 68 | 278 |
| EDLHLGA23009V012223 | 2 | 7 | 198 |
| **Total** | | **838** | |

100% of chunks carry a section breadcrumb; none exceed the token cap.

### Why hybrid retrieval — measured, not assumed

Dense and sparse retrieval, run separately over the same 838 chunks with real
`text-embedding-3-small` vectors. Each column wins queries the other loses:

| Query | Dense (semantic) | Sparse (keyword) |
|---|---|---|
| "What happens if I do not make a claim for a year?" | `4. Submit claim` ❌ | `5.1 Cumulative Bonus` ✅ |
| "Can I claim for an ambulance?" | `7. Air Ambulance` ✅ | `Well Baby Well Mother` ❌ |
| "Is cataract surgery excluded?" | `SECTION D) EXCLUSIONS` ✅ | `SECTION D) EXCLUSIONS` ✅ |
| "What is the limit on daily room rent?" | `42. Renewal` ❌ | `3. Must have been prescribed…` ❌ |

Dense handles vocabulary mismatch — "Can I claim for an ambulance?" retrieves
`7. Air Ambulance` at 0.62 similarity with no shared keyword. Sparse handles the
inverse: nobody phrases a question as "cumulative bonus", but that is the clause
that answers it, and only exact matching finds it.

Row four is the honest one: neither method answers it alone.

**After fusion, all four are answered.** Every one of those queries now retrieves
both dense and sparse hits, and "daily room rent" surfaces *"We will pay the
amount of rent You…"* — a chunk dense ranked 6th and sparse ranked 1st. Fusing
them puts it on top.

One subtlety cost real quality before it was caught. Postgres' `plainto_tsquery`
**ANDs** every term, so "What happens if I do not make a claim for a year?"
compiles to `happen & make & claim & year` and matches almost nothing — the
sparse half was silently dead for natural-language questions while looking
healthy when tested with bare keywords. Rewriting the operators to OR restores
it; `ts_rank_cd` still ranks by how many and how rare the matched terms are.

### Measuring quality — the evaluation harness

A wrong answer here is a plausible sentence, not a crash, so no unit test
catches it. `eval/` scores retrieval and answer quality against a golden set of
30 questions written by reading the source policies — phrased the way a user
would phrase them ("what happens if I don't claim for a year"), not the way the
document does ("cumulative bonus"), because that mismatch is the thing retrieval
has to bridge.

| Metric | Baseline | What it catches |
|---|---:|---|
| recall@5 | 84.6% | the answering text never reached the model |
| MRR | 59.2% | it reached the model, but buried |
| term accuracy | 50.0% | the answer omits the figure it must state |
| faithfulness | 78.3% | claims not supported by the retrieved excerpts |
| refusal accuracy | 90.0% | inventing an answer the corpus cannot support |

**The first version of this harness reported recall@5 of 100%.** It scored a hit
when any *filename* in the expected list appeared — and with six documents and
two or three acceptable per question, that is nearly free. Meanwhile the model
was answering the wrong question entirely: asked what happens after a claim-free
year, it returned a clause about claim time limits. Scoring on the *answering
text* instead dropped recall@5 to 84.6% and MRR from 87.8% to
59.2%.

Those lower numbers are the useful ones. They point at four real retrieval
misses, which cascade into every answer failure below them — that is the work
queue, and it did not exist while the metric said everything was fine.

The gate runs in CI as a job the Docker build depends on, because a workflow
nothing depends on blocks nothing when it goes red. Pull requests are scored on
retrieval only — no answer generation, so no spend — against the committed
baseline. The full run including faithfulness judging happens nightly.

Where no API key is available, as on a fork's pull request, embeddings are
deterministic noise and every ranking metric is meaningless. The previous
version silently dropped the gate and reported success. It now runs a smoke
check instead: every answerable question must retrieve *something*. That still
catches a dead tsv trigger, a broken migration or an empty corpus, and it fails
rather than passing vacuously.

### What an audit of the first four phases found

Every phase was green — tests passing, CI clean — and eleven real gaps were
still there. They are worth listing because none of them would have announced
itself:

**Scoping filtered after retrieval.** Asking a question about one document
fetched the global top-6 and *then* discarded everything from other documents,
so a scoped question returned nothing whenever other documents filled the
ranking — including when the named document contained the answer. The filter is
now a predicate inside both retrieval CTEs.

**The pgvector test guard never ran.** `skipif(not pgvector_available)` was
passed the *function*, so `not <function>` was permanently `False` and nothing
ever skipped; the flag it read was also set inside a fixture that runs after
collection, so a corrected call would have skipped everything instead. Broken in
both directions, which is why it looked like it worked.

**Per-user rate limiting was dead.** The key function preferred
`request.state.user_id`, and nothing ever set it — so every authenticated caller
shared one IP bucket, exactly what keying by user exists to prevent.

**A missing API key was silent.** Without one the provider fell back to
deterministic fake vectors, indexed the corpus with noise and marked every
document `ready`. Production now refuses to start.

**No `.dockerignore`.** `COPY . .` would have baked the local `.env` — real key,
real database password — into an image layer, where deleting it later does not
remove it. The image also shipped pytest, ruff and mypy.

**The quality gate did not gate.** It lived in a workflow nothing depended on,
its path filter missed the config file owning every retrieval knob, and without
an API key it dropped `--check` and reported success while measuring nothing.

Also: no timeouts on any OpenAI call (inheriting a 600s default by accident),
`chat_sync` returning a bare 500 on upstream failure, `MAX_PDF_PAGES` declared
but never enforced, uploads stranded in `pending` forever after a Redis blip,
and no coverage measurement anywhere.

The lesson worth keeping is that all eleven passed a green test suite. Tests
prove the paths you thought of still work; they say nothing about the ones you
never wrote down.

### The frontend

React, TypeScript, Vite, Tailwind and TanStack Query, in `frontend/`. Three
decisions there are worth stating, because each has a wrong version that looks
identical until it fails.

**The access token lives in memory, never `localStorage`.** Anything stored
there is readable by any script that ends up on the page, so one XSS becomes a
stolen session that outlives the tab. Losing the token on reload is the point —
the httpOnly refresh cookie restores the session, and the app asks for a new
access token before deciding whether to show the login screen. Skip that step
and every reload looks like a logout.

**Concurrent 401s share one refresh.** Refresh tokens rotate, and replaying a
rotated one is treated as theft: the backend revokes the entire family. A page
that fires four queries on mount would send four refresh requests the moment the
token expired and log the user out — a bug that only appears after the access
token's lifetime, which is exactly long enough for it never to show up while
you are working on it. All callers await a single in-flight refresh instead.

**Streaming is parsed by hand.** `EventSource` cannot be used — it only issues
GET requests and cannot set an `Authorization` header, and the chat endpoint is
an authenticated POST. So the body is read from `fetch` and parsed in
`src/lib/sse.ts`, which exists because network chunk boundaries have nothing to
do with message boundaries: one event can arrive split across three reads. Its
tests cover split events, CRLF endings, comments, keep-alives, and a multi-byte
character split mid-character.

A failed stream arrives as an in-band `error` event, not an HTTP status,
because by then the response has already begun — so a failure looks like a
request that succeeded and stopped early, and watching for that event is the
only way to tell.

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

Then the frontend, in another shell:

```bash
cd frontend
npm install
npm run dev             # http://localhost:5173
```

It proxies `/api` to the backend so the browser sees one origin — without that
the refresh cookie is not sent and sessions fail to restore on reload while
everything else appears to work. See `frontend/README.md`.

Use `run.py` and `worker.py` rather than invoking `uvicorn` or `arq` directly.
Both build their event loop before anything else, because on Windows the default
is a `ProactorEventLoop` and psycopg3 cannot run on it — the worker in particular
would start, accept jobs, and then fail every one at the first query. They are
no-ops on Linux and macOS, so the same commands work everywhere.

A local Postgres already on 5432 will shadow the compose container silently; set
`POSTGRES_PORT` to move it.

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
| POST | `/api/chat` | Bearer | Ask a question → SSE stream of the answer |
| POST | `/api/chat/sync` | Bearer | Same, non-streaming (tests / eval harness) |
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
