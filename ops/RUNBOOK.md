# Runbook

How to deploy SafeShield, and what to do when it misbehaves.

---

## Deploying

### 1. Prepare the environment

Nothing in the repository contains a secret, and the config validator refuses to
start with `ENV=prod` and any development default — a wrong value is a crash at
boot, not a quiet compromise. Create a `.env` beside `docker-compose.yml`:

```bash
ENV=prod
DEBUG=false
JSON_LOGS=true

# 48 bytes of randomness. Reusing the dev key means anyone can mint a valid token.
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

DATABASE_URL=postgresql+psycopg://safeshield:<password>@postgres:5432/safeshield
POSTGRES_PASSWORD=<same password>
POSTGRES_EXPORTER_DSN=postgresql://safeshield:<password>@postgres:5432/safeshield?sslmode=disable

OPENAI_API_KEY=sk-...

# https only, and it must match the deployed frontend exactly. A mismatch here
# is a CORS failure in the browser before the request is ever sent.
CORS_ORIGINS=["https://safeshield.example"]

GRAFANA_PASSWORD=<password>
GRAFANA_ROOT_URL=https://grafana.safeshield.example
```

`chmod 600 .env`. It is gitignored, and `.dockerignore` keeps it out of the
image — a `COPY . .` without that would bake it into a layer, where deleting it
later does not remove it.

### 2. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The overlay removes the source bind-mount, turns off `DEBUG` and `RELOAD`,
switches to JSON logs, and unpublishes Postgres, Redis, Prometheus and Grafana
from the public interface. The base file alone is a *development* configuration
and every one of those differences is silent — the app serves traffic either
way.

### 3. Verify, in this order

```bash
# 1. The process is alive.
curl -fsS http://localhost:8000/api/health

# 2. Its dependencies answer. This is the one that matters.
curl -fsS http://localhost:8000/api/ready | jq
#    {"status":"ready","checks":{"database":"ok","redis":"ok"}}

# 3. Migrations are at head.
docker compose exec api alembic current

# 4. Prometheus can see everything. Expect 5 targets up.
curl -fsS localhost:9090/api/v1/targets | jq '[.data.activeTargets[] | {job:.labels.job, health}]'

# 5. The corpus is indexed. Zero here means retrieval will refuse everything.
docker compose exec api python -c "
from app.compat import asyncio_run
from sqlalchemy import text
from app.db import SessionLocal
async def main():
    async with SessionLocal() as db:
        print('chunks:', (await db.execute(text('SELECT count(*) FROM chunks'))).scalar())
asyncio_run(main())"

# 6. An answer actually generates end to end.
TOKEN=$(curl -sX POST localhost:8000/api/auth/demo | jq -r .access_token)
curl -sN -X POST localhost:8000/api/chat/sync \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"Is cataract treatment excluded from cover?"}' | jq '.grounded, (.citations|length)'
```

Step 6 is the only one that proves the whole pipeline. Steps 1–5 can all pass
while answers are nonsense — which is exactly what happens with a missing
`OPENAI_API_KEY` outside production, where the corpus is indexed with
deterministic noise and every document still reports `ready`.

### Seeding the sample corpus

```bash
docker compose exec api python scripts/seed_corpus.py
```

This spends real money on embeddings (~838 chunks). It is idempotent: re-running
replaces chunks rather than duplicating them.

### Scaling past one replica

Two things in this setup assume a single API container:

- **Migrations run in the API's start command.** With replicas that is a race.
  Move `alembic upgrade head` to a one-shot job that completes before the
  rollout starts.
- **Uploaded PDFs are written to a container-local directory.** Two replicas
  will not see each other's files, so an upload handled by one and ingested by
  another fails. Mount shared storage or move to object storage.

Neither is a problem at one replica, and both are silent when they become one.

---

## Monitoring

| Service | Local URL | Notes |
|---|---|---|
| Grafana | <http://localhost:3001> | Dashboards provisioned from `ops/grafana/dashboards/` |
| Prometheus | <http://localhost:9090> | Rules in `ops/prometheus/alerts.yml` |
| API metrics | <http://localhost:8000/metrics> | |
| Worker metrics | <http://localhost:9100/metrics> | Separate process, separate port |

Dashboards and datasources are provisioned as code, and UI edits are overwritten
on restart. That is deliberate: a dashboard that only exists in someone's
browser is not part of the project, and a change to one should be reviewed like
any other.

**Service health** answers "is it up and fast". **RAG quality and cost** answers
"is it any good, and what is it spending" — which the first cannot see at all. A
pipeline that retrieves nothing, refuses every question and returns 200 in 40ms
looks perfect on a service dashboard.

---

## Diagnosing

Every log line carries a `request_id`, and the same id comes back in the
`X-Request-ID` response header. A user reporting "it failed" is handing you the
key:

```bash
docker compose logs api | grep '"request_id":"<id>"'
```

### Answers are all refusals

The pipeline is running and retrieval is not finding anything.

1. Check `chunks` is non-empty (verification step 5). An empty index refuses
   everything, correctly.
2. Open **RAG quality → Which retriever found the results**. If `sparse` and
   `both` are flat while queries keep arriving, the lexical half is dead — check
   the `tsv` trigger and the `plainto_tsquery` rewrite. This has happened
   before: `plainto_tsquery` ANDs every term, so natural-language questions
   compiled to `happen & make & claim & year` and matched nothing, while
   latency and error rate looked perfect.
3. Check **Top fused score**. Collapsing towards zero means nothing matches on
   either retriever.
4. Confirm the embedding model has not changed. Vectors written by a different
   model are in a different space, and comparing across them returns noise with
   no error raised anywhere.

### Uploads never finish

`safeshield_documents_pending` sustained above zero.

1. Is the worker up? `docker compose ps worker` and check target `worker` in
   Prometheus.
2. Is Redis reachable? `/api/ready` reports it.
3. The upload endpoint deliberately swallows a queue failure — the bytes are on
   disk and the row is saved, so failing the request would lose more. The cron
   sweep re-drives anything pending longer than `INGEST_STRANDED_AFTER_SECONDS`
   (default 15 minutes). Forcing it early:

```bash
docker compose exec worker python -c "
from app.compat import asyncio_run
from arq import create_pool
from app.worker.tasks import _redis_settings, requeue_stranded_documents
async def main():
    pool = await create_pool(_redis_settings())
    print('requeued:', await requeue_stranded_documents({'redis': pool}))
    await pool.aclose()
asyncio_run(main())"
```

4. A document stuck in `processing` rather than `pending` means the worker died
   mid-job. ARQ retries three times; after that the document is marked `failed`
   with the reason, visible in the UI.

### Token spend spiked

1. **RAG quality → Token burn** shows prompt, completion and embedding
   separately. A spike in *embedding* is someone uploading; a spike in
   *completion* is someone asking.
2. Rate limits are per user (`RATE_LIMIT_CHAT`, default 20/hour). Check whether
   one account is looping.
3. To stop spend immediately, scale the API to zero — the worker will not
   embed anything without uploads:

```bash
docker compose stop api
```

### Everything is slow

1. **Service health → Latency percentiles**. A wide p50/p99 gap means a slow
   minority, which is what users report.
2. Check Postgres connections. Above 80% of `max_connections`, requests queue
   waiting for a connection and every route slows at once.
3. Retrieval latency includes the query embedding call, which is usually the
   slow part. If `p95 retrieval` is high but `p95 time to first token` is not
   much higher, the problem is OpenAI's embedding endpoint, not the database.

---

## Rolling back

Images are tagged by commit sha in CI:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps api worker
```

after pointing the tag at the previous sha.

**Migrations do not roll back automatically.** They are tested reversible in CI
(`alembic downgrade base && alembic upgrade head`), but a downgrade that drops a
column loses the data in it. Check what the migration does before running:

```bash
docker compose exec api alembic downgrade -1
```

If the new code is compatible with the old schema — which it usually is, since
migrations here are additive — roll back the image and leave the schema alone.
