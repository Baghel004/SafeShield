# Going live for free — Neon + Render

A second way to deploy, built to cost **nothing** and to need **none of the
command-line tools** the AWS path requires. There is no `aws`, `terraform`,
`helm`, or `kubectl` here, so the Windows Application Control policy that blocked
those is irrelevant. Render builds the app straight from your GitHub repo.

This runs a smaller shape than the EKS deployment: one web instance instead of a
Kubernetes cluster, no separate worker, and no Redis. It is the right choice for
a live portfolio link. The full scalable version (Helm + Terraform for EKS) is
still in `deploy/` and unchanged — `render.yaml` and this guide are additive.

**What it costs:** $0 for hosting. The only spend is OpenAI usage per question
(pennies), on the key you already have.

**One limitation to know:** Render's free web service **sleeps after 15 minutes
of no traffic**. The first request after it sleeps takes ~50 seconds to wake it.
The frontend shows a "backend offline / waking up" state during that, so it is
not broken — just slow on the first hit. For a demo you click occasionally,
that is fine.

---

## The pieces

```
   Browser
     │
     ├──────────────▶  Render static site   (the React frontend, free)
     │                        │ HTTPS + JWT
     ▼                        ▼
   Render web service  ──▶  FastAPI app      (one instance, free, sleeps when idle)
                              │
                              ├──▶  Neon      (Postgres + pgvector, free)
                              └──▶  OpenAI    (your key)
```

No Redis, no worker: rate limiting runs in the instance's memory, and document
ingestion runs inside the API process. That is what `REDIS_ENABLED=false`
switches on, and `render.yaml` sets it for you.

---

## Step 1 — Database: Neon

You already used Neon earlier in this project, so you may have an account.

1. Go to <https://neon.tech>, sign in (GitHub login is easiest), and create a
   project — any name, region closest to you.
2. On the project dashboard, find the **connection string**. It looks like:
   ```
   postgresql://user:password@ep-something.aws.neon.tech/neondb?sslmode=require
   ```
3. **Change the scheme** from `postgresql://` to `postgresql+psycopg://` — the
   app uses the psycopg driver. So the value you will use is:
   ```
   postgresql+psycopg://user:password@ep-something.aws.neon.tech/neondb?sslmode=require
   ```
   Keep this somewhere for the next steps. This is `DATABASE_URL`.

Neon's free tier includes the `pgvector` extension the app needs; the migration
enables it automatically.

---

## Step 2 — Load the sample corpus into Neon

Render will run the schema migration itself, but the sample policies have to be
embedded and inserted once, from your machine, because the PDFs are not in the
container image. This uses **Python**, which is not affected by the Application
Control policy, so it runs fine in a normal terminal.

In the project's `backend` folder (your Desktop copy is fine), with the
virtualenv you already have:

```bash
cd backend
# activate the venv you used during development:
#   Windows PowerShell:  .venv\Scripts\Activate.ps1
#   Git Bash / WSL:      source .venv/Scripts/activate   (or .venv/bin/activate)

# point it at Neon and your OpenAI key for this one run:
export DATABASE_URL="postgresql+psycopg://user:password@ep-something.aws.neon.tech/neondb?sslmode=require"
export OPENAI_API_KEY="sk-..."

alembic upgrade head           # create the tables in Neon
python scripts/seed_corpus.py  # embed + insert the 6 sample policies (~838 chunks)
```

(On PowerShell, use `$env:DATABASE_URL="..."` and `$env:OPENAI_API_KEY="..."`
instead of `export`.)

This spends a few cents on embeddings and takes a couple of minutes. When it
prints `Done. 838 chunks across 6 documents.`, the database is ready. You only
do this once — the data lives in Neon and survives everything else.

---

## Step 3 — Deploy both services on Render

1. Go to <https://render.com> and sign up with your **GitHub** account.
2. **New +** → **Blueprint**.
3. Connect your `SafeShield` repository and pick the branch you are deploying
   (e.g. `phase-1-fastapi-auth`). Render finds `render.yaml` and shows two
   services: **safeshield-api** and **safeshield-frontend**.
4. It will ask for the values marked secret. Set:
   - On **safeshield-api**:
     - `DATABASE_URL` → your Neon string from Step 1.
     - `OPENAI_API_KEY` → your key.
     - `CORS_ORIGINS` → leave as a placeholder for now, e.g. `["https://example.com"]`;
       you will fix it in Step 4 once you know the frontend URL.
   - On **safeshield-frontend**:
     - `VITE_API_BASE_URL` → leave as a placeholder for now, e.g. `https://example.com`.
5. Click **Apply**. Render builds both. The backend build takes a few minutes
   (it builds the Docker image); the frontend is quick.

---

## Step 4 — Wire the two together

After the first deploy, each service has a URL, shown on its Render page:
`https://safeshield-api.onrender.com` and
`https://safeshield-frontend.onrender.com` (the exact names may have a suffix if
those were taken — use whatever Render shows).

Now set the two cross-referencing values properly:

1. **safeshield-api** → Environment → set `CORS_ORIGINS` to the frontend URL, as
   a JSON list:
   ```
   ["https://safeshield-frontend.onrender.com"]
   ```
2. **safeshield-frontend** → Environment → set `VITE_API_BASE_URL` to the API
   URL (no trailing slash):
   ```
   https://safeshield-api.onrender.com
   ```
3. Save each. Render redeploys automatically. The frontend must rebuild for the
   API URL to take effect (it is baked in at build time), so if it does not
   redeploy on its own, hit **Manual Deploy → Deploy latest commit** on the
   frontend.

Why both: the browser blocks the frontend from calling the API unless the API
lists the frontend's exact origin in `CORS_ORIGINS`, and the frontend only knows
where the API is because the URL was compiled into its build.

---

## Step 5 — Check it

Open the **frontend** URL. Click **Try the demo** and ask a question, e.g.
*"Is cataract treatment excluded from cover?"*

- If the backend was asleep, the first request takes ~50 seconds and you may see
  the "backend offline" banner briefly — wait and it wakes.
- You should then get an answer with numbered citations.

That is the whole app, live, on a permanent link, for free. Put the **frontend**
URL on your CV.

---

## If something goes wrong

- **Frontend loads but every request fails / "backend offline" that never
  clears:** the two URLs are not wired. Re-check Step 4 — `CORS_ORIGINS` on the
  API must be exactly the frontend origin (JSON list, `https://`, no trailing
  slash), and the frontend must have been **rebuilt** after setting
  `VITE_API_BASE_URL`.
- **Login/demo seems to work but you are logged out on refresh:** the cookie is
  not surviving cross-subdomain. Confirm `COOKIE_SAMESITE=none` and
  `COOKIE_SECURE=true` on the API (both are in `render.yaml`).
- **Answers all say "I couldn't find this…":** the corpus was not seeded, or was
  seeded into a different database than `DATABASE_URL` points at. Re-run Step 2
  against the same Neon string.
- **Backend deploy fails at start with an "InsecureConfiguration" error:** the
  prod safety check is doing its job — read which value it names. Usually
  `OPENAI_API_KEY` unset, or `CORS_ORIGINS` still on an `http://` placeholder.
- **First request is always slow:** that is the free tier sleeping after 15 min
  idle, not a bug. Upgrading the API service to Render's cheapest paid instance
  removes the sleep if you ever want to.

---

## What this shares with the "real" deployment

The application code is identical — same image, same endpoints, same RAG
pipeline. The only difference is configuration: `REDIS_ENABLED=false` folds the
worker into the API and keeps rate limiting in memory. Everything you built for
the EKS deployment (the Helm chart, the Terraform, the metrics, the storage
interface) is untouched and still on GitHub as evidence of the scalable design —
this is simply the version that fits in a free tier and needs no local tooling
to ship.
