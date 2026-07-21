#!/usr/bin/env bash
# Publish the frontend to S3 + CloudFront (AWS free tier). Run from WSL/Ubuntu,
# where terraform and aws are not blocked by Windows Application Control.
#
#   deploy/scripts/deploy-frontend.sh https://safeshield-api.onrender.com
#
# The argument is the backend's URL (the Render service), because it is compiled
# into the frontend bundle at build time -- the browser has to know where the
# API lives, and a static site cannot be told at runtime.
#
# This is idempotent: run it again any time you change the frontend or the
# backend URL. It creates the bucket and CDN on the first run and reuses them
# after.
set -euo pipefail

API_URL="${1:-}"
if [ -z "$API_URL" ]; then
  echo "usage: $0 <backend-api-url>" >&2
  echo "  e.g. $0 https://safeshield-api.onrender.com" >&2
  exit 1
fi

need() { command -v "$1" >/dev/null || { echo "missing required tool: $1" >&2; exit 1; }; }
for t in terraform aws npm git; do need "$t"; done
aws sts get-caller-identity >/dev/null 2>&1 \
  || { echo "AWS credentials not configured -- run 'aws configure' in this terminal." >&2; exit 1; }

cd "$(git rev-parse --show-toplevel)"
TF=deploy/terraform-frontend
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

# --- 1. The bucket and CDN (free tier: S3 + CloudFront, no custom domain) ----
step "Creating / updating S3 + CloudFront"
terraform -chdir="$TF" init -input=false
terraform -chdir="$TF" apply -input=false -auto-approve

BUCKET="$(terraform -chdir="$TF" output -raw bucket_name)"
DIST="$(terraform -chdir="$TF" output -raw distribution_id)"
SITE="$(terraform -chdir="$TF" output -raw site_url)"

# --- 2. Build the frontend, pointed at the backend ---------------------------
step "Building the frontend against $API_URL"
# npm ci (not install) so the build uses exactly the locked versions and does
# not depend on whatever node_modules happens to be lying around.
( cd frontend && npm ci && VITE_API_BASE_URL="$API_URL" npm run build )

# --- 3. Upload and invalidate ------------------------------------------------
step "Uploading to s3://$BUCKET"
aws s3 sync frontend/dist "s3://$BUCKET" --delete
# Only index.html needs invalidating -- Vite fingerprints the asset filenames,
# so a changed asset already has a new URL and is fetched fresh.
aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/index.html' >/dev/null

step "Frontend published"
echo "  Live at:  $SITE"
echo
echo "  Now set this on the Render backend (Environment -> CORS_ORIGINS) and redeploy it:"
echo "      [\"$SITE\"]"
echo
echo "  Without that, the browser will block every request from the site to the API."
