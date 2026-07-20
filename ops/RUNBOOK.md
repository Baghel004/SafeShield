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

This compose setup is single-host and single-replica by design. Two things
would break if it were not, and both are fixed in the Kubernetes path below
rather than here:

- **Migrations run in the API's start command.** With more than one replica two
  pods start together and both run `alembic upgrade head` against the same
  database. The Helm chart runs them as a pre-upgrade hook that completes first.
- **Uploads go to a container-local directory.** A file written by one replica
  is invisible to the others and to the worker. `STORAGE_BACKEND=s3` moves them
  to object storage; the Helm chart defaults to it.

Both are silent when they become a problem, which is why the chart defaults to
two API replicas — a configuration that would expose them immediately.

---

## On-demand deploy (the intended workflow)

The backend is not meant to run 24/7. It is brought up for a session and torn
down after, which turns a ~$210/month stack into ~$0.28/hour. Two scripts wrap
the whole thing:

```bash
cp deploy/terraform/ondemand.tfvars.example deploy/terraform/ondemand.tfvars
# edit it: set billing_alarm_email, confirm deletion_protection = false

deploy/scripts/up.sh      # ~25 min cold: infra, image, release, seed, frontend
# ... demo ...
deploy/scripts/down.sh    # uninstall, destroy, and verify nothing survived
```

`up.sh` provisions the infrastructure, builds and pushes the image tagged by
commit sha, installs the chart (migrations run first as a hook), re-seeds the
corpus — the database is wiped by every teardown, so this is not optional — and
publishes the frontend pointed at the new backend. It prints the URL at the end.

`down.sh` is the one that matters for the bill. It **uninstalls the Helm release
before destroying the cluster**, because the ALB was created by the Ingress
controller and Terraform does not know about it — destroy the cluster first and
the load balancer is orphaned, billing with no owner. Then it destroys the
infrastructure and, crucially, **does not trust that destroy succeeded**: it
asks AWS whether any EKS cluster, NAT gateway, load balancer, RDS instance,
ElastiCache group or tagged EC2 instance is still running, and exits non-zero if
so. A surprise bill on an on-demand stack almost never comes from forgetting to
tear down — it comes from a teardown that half-failed and left a NAT gateway up
for a week.

**What survives a teardown, by design:** the frontend (CDN, permanent, ~$1–3/mo),
the ECR images, the uploads bucket, and the two Secrets Manager entries — all
near-free. The database and its 838 indexed chunks do not survive, which is why
`up.sh` re-seeds every time (~$0.02 of embeddings).

The `deletion_protection = false` in `ondemand.tfvars` is what lets `destroy`
remove the database at all. With the default `true` — correct for a permanent
deployment — RDS refuses to drop and you are left paying for a database you
believed you had removed.

The manual steps below are the same operations the scripts automate, kept for
reference and for a permanent (non-on-demand) deployment.

---

## Deploying to Kubernetes (AWS)

`deploy/terraform` provisions the infrastructure; `deploy/helm/safeshield`
deploys the application onto it.

**What has been verified here, and what has not.** The chart lints, renders 14
resources, and every one validates against the Kubernetes 1.30 schema including
the ServiceMonitor CRDs. Both Terraform root modules are formatted, initialise
against the real AWS provider and modules, and validate. The deploy scripts pass
shellcheck. None of it has been applied to a live cluster or a real AWS account —
that needs credentials and costs money, so the first apply is yours. Treat the
steps below as reviewed, not rehearsed.

### 1. Infrastructure

```bash
cd deploy/terraform
terraform init
terraform plan      # read this properly the first time
terraform apply
```

Creates a VPC across two AZs, an EKS cluster, RDS Postgres 16 (pgvector is
available from 15.2; the migration issues `CREATE EXTENSION vector`),
ElastiCache Redis, an S3 bucket for uploads, ECR, and two IRSA roles.

Run continuously this costs roughly **$210/month** — EKS control plane (~$73),
two t3.medium nodes (~$60), NAT gateway (~$32), RDS + ElastiCache (~$45), ALB
(~$18). That is why the intended workflow is **on-demand**: bring it up for a
session, tear it down after. Hourly that is about **$0.28**, so a four-hour demo
is ~$1.20. The tooling for that is in the next section — prefer it over the raw
commands here, which leave the stack running.

### 2. Secrets

Terraform creates the Secrets Manager entries but never writes the application
values into them, because anything Terraform sets lands in state in plaintext:

```bash
aws secretsmanager put-secret-value \
  --secret-id safeshield-prod/app \
  --secret-string '{"JWT_SECRET":"<48 random bytes>","OPENAI_API_KEY":"sk-..."}'
```

The database and Redis URLs are written by Terraform, since it generates the
password and there is no way for it not to know it.

Then get both into the cluster as `safeshield-secrets`. External Secrets
Operator is the reason `external_secrets_role_arn` exists; `kubectl create
secret generic` works for a first deploy.

### 3. Deploy

```bash
aws eks update-kubeconfig --region ap-south-1 --name safeshield-prod
kubectl create namespace safeshield

helm upgrade --install safeshield deploy/helm/safeshield \
  --namespace safeshield \
  --set image.repository=$(terraform -chdir=deploy/terraform output -raw ecr_repository_url) \
  --set image.tag=$(git rev-parse --short HEAD) \
  --set config.storage.bucket=$(terraform -chdir=deploy/terraform output -raw uploads_bucket) \
  --set serviceAccount.annotations."eks\\.amazonaws\\.com/role-arn"=$(terraform -chdir=deploy/terraform output -raw app_role_arn) \
  --set ingress.host=api.safeshield.example \
  --set 'config.corsOrigins[0]=https://safeshield.example' \
  --wait
```

`image.tag` has no default and the chart **fails to render** without it. That is
deliberate: `latest` makes a rollout unreproducible and a rollback meaningless,
because the tag has already moved to the thing you are rolling back from.

Migrations run as a `pre-install,pre-upgrade` hook that must complete before any
new pod starts. They used to be in the API container's start command, which is a
race the moment there is more than one replica.

### 4. Verify

```bash
kubectl -n safeshield get pods
kubectl -n safeshield logs job/safeshield-migrate      # only if the hook failed
kubectl -n safeshield port-forward svc/safeshield-api 8080:80

curl -fsS localhost:8080/api/ready | jq     # database and redis both "ok"
```

Then seed the corpus, or every question refuses:

```bash
kubectl -n safeshield exec deploy/safeshield-api -- python scripts/seed_corpus.py
```

### Things worth knowing before the first apply

- **`STORAGE_BACKEND=s3` is not optional above one replica.** With `local`, an
  upload handled by one pod is invisible to the worker and to every other pod,
  and ingestion fails with a missing file — only under the horizontal scaling
  the chart is built for. The `uploads` volume in the pod spec is an `emptyDir`
  on purpose: it is scratch space for the temporary copy an S3 object is
  downloaded into, not storage.
- **`automountServiceAccountToken: false`** is set on the service account. IRSA
  should be unaffected — the EKS webhook injects its own projected token with an
  `sts.amazonaws.com` audience — but this is the first thing to flip if a pod
  cannot reach S3.
- **NetworkPolicy needs the VPC CNI's `enableNetworkPolicy`**, which the
  Terraform sets. Without it the policies are accepted by the API server and
  silently ignored, which is worse than not having them.
- **The Ingress needs the AWS Load Balancer Controller** installed in the
  cluster; the annotations assume it. Without it the Ingress is created and
  never gets an address, with no obvious error.

### The frontend

Hosted separately from the backend, in `deploy/terraform-frontend`, because the
two have opposite lifecycles — the site stays up permanently while the backend
comes and goes. Coupling them in one state file would tie a permanent CDN to an
on-demand teardown.

```bash
terraform -chdir=deploy/terraform-frontend init
terraform -chdir=deploy/terraform-frontend apply
```

Creates a private S3 bucket and a CloudFront distribution reaching it through
Origin Access Control — nothing is served from S3 directly, so the CDN's caching
and TLS are never bypassed. With no `domain_name` set it uses the free
`*.cloudfront.net` domain, which needs no DNS and no certificate.

The build must be pointed at the backend at build time, because the API URL is
compiled into the bundle:

```bash
cd frontend
VITE_API_BASE_URL="https://<alb-hostname>" npm run build
aws s3 sync dist "s3://$(terraform -chdir=../deploy/terraform-frontend output -raw bucket_name)" --delete
aws cloudfront create-invalidation \
  --distribution-id $(terraform -chdir=../deploy/terraform-frontend output -raw distribution_id) \
  --paths '/index.html'
```

Only `/index.html` is invalidated: Vite fingerprints the asset filenames, so a
changed asset already has a new URL. That backend origin must also be in the
API's `CORS_ORIGINS`, or the browser blocks every request before it is sent —
`up.sh` passes it through as `config.corsOrigins[0]` automatically.

Because the site outlives the backend, it checks `/api/health` on load and shows
a "backend is currently offline" banner when the cluster is down, rather than a
login form that fails for reasons the visitor cannot see.

### Rolling back

```bash
helm rollback safeshield --namespace safeshield
```

Schema changes do not roll back with it. Migrations here are additive, so the
previous image almost always runs against the newer schema — prefer rolling the
image back and leaving the database alone.

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
