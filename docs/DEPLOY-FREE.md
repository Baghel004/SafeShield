# Going live for free — S3/CloudFront + Render + Neon

A free deployment that costs **nothing** to host. It is a small hybrid:

- **Frontend** on **AWS S3 + CloudFront** — both free-tier eligible, and
  CloudFront's free tier (1 TB/month) is permanent, not a 12-month trial.
- **Backend** on a **Render** free web service, built straight from GitHub.
- **Database** on **Neon** (free Postgres + pgvector).

This is a smaller shape than the EKS deployment — one web instance, no separate
worker, no Redis — and it is the right choice for a live portfolio link. The
full scalable version (Helm + Terraform for EKS) is still in `deploy/` and
unchanged; this guide and `render.yaml` are additive.

**What it costs:** $0 for hosting. The only spend is OpenAI usage per question
(pennies), on the key you already have.

**Two things to know before you start:**

1. **The frontend needs `terraform` and `aws`, which Windows blocks** with the
   Application Control policy — so that one part runs from **WSL (Ubuntu)**. It
   is a tiny, fast apply (just a bucket and a CDN, no cluster), nothing like the
   EKS pain. The backend and database need no local tools at all.
2. **Render's free web service sleeps after 15 minutes of no traffic.** The
   first request after it sleeps takes ~50 seconds to wake it, during which the
   frontend shows a "backend offline / waking up" state. Not broken — just slow
   on the first hit, which is fine for a demo.

---

## The pieces

```
   Browser
     │
     ├───────────────▶  CloudFront + S3     (the React frontend, AWS free tier)
     │                        │ HTTPS + JWT
     ▼                        ▼
   Render web service  ───▶  FastAPI app     (one instance, free, sleeps when idle)
                              │
                              ├──▶  Neon      (Postgres + pgvector, free)
                              └──▶  OpenAI    (your key)
```

No Redis, no worker: rate limiting runs in the instance's memory, and document
ingestion runs inside the API process. That is what `REDIS_ENABLED=false`
switches on, and `render.yaml` sets it for you.

The two halves are wired to each other by two values: the frontend build is
compiled with the backend's URL (`VITE_API_BASE_URL`), and the backend is told
the frontend's URL (`CORS_ORIGINS`) so the browser is allowed to call it.

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

## Step 3 — Deploy the backend on Render

1. Go to <https://render.com> and sign up with your **GitHub** account.
2. **New +** → **Blueprint**.
3. Connect your `SafeShield` repository and pick the branch you are deploying
   (e.g. `phase-1-fastapi-auth`). Render finds `render.yaml` and shows one
   service: **safeshield-api**.
4. It will ask for the values marked secret. Set:
   - `DATABASE_URL` → your Neon string from Step 1.
   - `OPENAI_API_KEY` → your key.
   - `CORS_ORIGINS` → leave a placeholder for now, e.g. `["https://example.com"]`;
     you will set it to the real CloudFront URL in Step 5.
5. Click **Apply**. Render builds the Docker image and starts the service (a few
   minutes). When it is live, copy its URL from the service page — something
   like `https://safeshield-api.onrender.com`. **Keep this; it is the backend
   URL the frontend needs.**

---

## Step 4 — Publish the frontend to S3 + CloudFront (from WSL)

This is the one part that needs `terraform` and `aws`, so it runs in **Ubuntu
(WSL)** where they are not blocked. If you have not set those up in WSL yet, do
Step 1 of `docs/DEPLOY.md` first (just the tool installs and `aws configure`);
you do **not** need any of the EKS steps.

Then, from the project in your Linux home:

```bash
cd ~/SafeShield
bash deploy/scripts/deploy-frontend.sh https://safeshield-api.onrender.com
```

Use your real backend URL from Step 3 as the argument. The script:

1. Creates the S3 bucket and CloudFront distribution (free tier, no custom
   domain) — or reuses them on later runs.
2. Builds the frontend with that backend URL compiled in.
3. Uploads the build to S3 and refreshes the CDN.

It finishes by printing the live site URL and the exact `CORS_ORIGINS` value to
set next, e.g.:

```
  Live at:  https://d111111abcdef8.cloudfront.net
  Now set this on the Render backend (Environment -> CORS_ORIGINS) and redeploy it:
      ["https://d111111abcdef8.cloudfront.net"]
```

Copy that CloudFront URL. (CloudFront takes a few minutes after the first
`apply` before the URL serves content — if it 404s at first, give it 5 minutes.)

---

## Step 5 — Wire the backend to the frontend

The browser will block the site from calling the API until the API explicitly
allows the site's origin. So set it:

1. On Render, open **safeshield-api** → **Environment**.
2. Set `CORS_ORIGINS` to the CloudFront URL as a JSON list, exactly as the
   script printed:
   ```
   ["https://d111111abcdef8.cloudfront.net"]
   ```
3. Save. Render redeploys the backend automatically.

The other direction was already handled in Step 4 — the frontend build has the
backend URL baked in, so nothing more to do there.

---

## Step 6 — Check it

Open the **CloudFront URL** in your browser. Click **Try the demo** and ask a
question, e.g. *"Is cataract treatment excluded from cover?"*

- If the Render backend was asleep, the first request takes ~50 seconds and you
  may see the "backend offline" banner briefly — wait and it wakes.
- You should then get an answer with numbered citations.

That is the whole app, live, on a permanent link, for free. Put the **CloudFront
URL** on your CV.

To update the frontend later (a code change, or a different backend URL), just
run `deploy/scripts/deploy-frontend.sh <backend-url>` again.

---

## If something goes wrong

- **Site loads but every request fails / "backend offline" that never clears:**
  the two URLs are not wired. Re-check that `CORS_ORIGINS` on the Render backend
  is exactly the CloudFront origin (JSON list, `https://`, no trailing slash),
  and that you ran `deploy-frontend.sh` with the correct backend URL (that URL
  is compiled into the build, so a wrong one means the site calls the wrong
  place — re-run the script to fix it).
- **The CloudFront URL 404s right after the first deploy:** a new distribution
  takes a few minutes to propagate. Wait 5 minutes and refresh.
- **Login/demo works but you are logged out on refresh:** the cookie is not
  surviving cross-site. Confirm `COOKIE_SAMESITE=none` and `COOKIE_SECURE=true`
  on the Render backend (both are in `render.yaml`).
- **Answers all say "I couldn't find this…":** the corpus was not seeded, or was
  seeded into a different database than `DATABASE_URL` points at. Re-run Step 2
  against the same Neon string.
- **Backend deploy fails at start with an "InsecureConfiguration" error:** the
  prod safety check is doing its job — read which value it names. Usually
  `OPENAI_API_KEY` unset, or `CORS_ORIGINS` still on an `http://` placeholder.
- **`deploy-frontend.sh` says a tool is missing or credentials are not
  configured:** you are in the wrong terminal or have not set AWS up in WSL. It
  must run in **Ubuntu**, with `terraform` and `aws` installed and
  `aws configure` done there — see Step 1 of `docs/DEPLOY.md`.
- **First request is always slow:** that is the Render free tier sleeping after
  15 min idle, not a bug. Upgrading the API to Render's cheapest paid instance
  removes the sleep if you ever want to. The CloudFront frontend never sleeps.

---

## What this shares with the "real" deployment

The application code is identical — same image, same endpoints, same RAG
pipeline. The only difference is configuration: `REDIS_ENABLED=false` folds the
worker into the API and keeps rate limiting in memory. And the frontend is the
same `deploy/terraform-frontend` (S3 + CloudFront) used by the EKS deployment —
here it simply points at a Render backend instead of an AWS one. Everything you
built for the scalable design (the Helm chart, the EKS Terraform, the metrics,
the storage interface) is untouched and still on GitHub as evidence of it; this
is the version that fits in a free tier.
