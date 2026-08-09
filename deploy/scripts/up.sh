#!/usr/bin/env bash
# Bring the on-demand backend up, end to end, and print the URL to hit.
#
# Ordered so each step depends only on what ran before it: infrastructure, then
# the image it will run, then the release, then the data, then the frontend
# that points at it. A failure stops the script rather than leaving a
# half-deployed stack that still bills.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
CLUSTER="safeshield-prod"
NAMESPACE="safeshield"
TF_BACKEND="deploy/terraform"
TF_FRONTEND="deploy/terraform-frontend"
TFVARS="${TFVARS:-ondemand.tfvars}"
CHART="deploy/helm/safeshield"

here() { cd "$(git rev-parse --show-toplevel)"; }
here

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
need() { command -v "$1" >/dev/null || { echo "missing required tool: $1" >&2; exit 1; }; }
# jq parses the Secrets Manager JSON; npm builds the frontend. Both are used
# far enough into the run that discovering they are missing at that point wastes
# the ~20 minutes already spent provisioning.
for t in aws terraform kubectl helm docker git jq npm; do need "$t"; done

# Fail now, not after apply, if the caller has no working AWS credentials.
aws sts get-caller-identity >/dev/null 2>&1 \
  || { echo "AWS credentials are not configured or have expired. Run 'aws configure' or refresh your SSO session." >&2; exit 1; }

IMAGE_TAG="$(git rev-parse --short HEAD)"

# --- 1. Infrastructure -------------------------------------------------------
step "Provisioning infrastructure (this is the slow part: ~20-25 min on a cold start)"
terraform -chdir="$TF_BACKEND" init -input=false
terraform -chdir="$TF_BACKEND" apply -input=false -auto-approve -var-file="$TFVARS"

ECR_URL="$(terraform -chdir="$TF_BACKEND" output -raw ecr_repository_url)"
UPLOADS_BUCKET="$(terraform -chdir="$TF_BACKEND" output -raw uploads_bucket)"
APP_ROLE_ARN="$(terraform -chdir="$TF_BACKEND" output -raw app_role_arn)"

# --- 2. Frontend hosting (idempotent; usually already up) --------------------
step "Ensuring frontend hosting exists"
terraform -chdir="$TF_FRONTEND" init -input=false
terraform -chdir="$TF_FRONTEND" apply -input=false -auto-approve
SITE_URL="$(terraform -chdir="$TF_FRONTEND" output -raw site_url)"
FRONTEND_BUCKET="$(terraform -chdir="$TF_FRONTEND" output -raw bucket_name)"
DISTRIBUTION_ID="$(terraform -chdir="$TF_FRONTEND" output -raw distribution_id)"

# --- 3. Image ----------------------------------------------------------------
step "Building and pushing the API image ($IMAGE_TAG)"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ECR_URL%/*}"
# linux/amd64 explicitly: the nodes are x86, and a build from an Apple-silicon
# or ARM host would otherwise push an image the cluster cannot run.
docker build --platform linux/amd64 -t "$ECR_URL:$IMAGE_TAG" ./backend
docker push "$ECR_URL:$IMAGE_TAG"

# --- 4. Cluster access -------------------------------------------------------
step "Configuring kubectl"
aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

step "Loading secrets into the cluster"
# Read the values Terraform stored (DB, Redis) and the ones set out of band
# (JWT, OpenAI) from Secrets Manager, and materialise the one Secret the chart
# references. Never echoed.
DB_SECRET="$(terraform -chdir="$TF_BACKEND" output -raw database_secret_name)"
APP_SECRET="$(terraform -chdir="$TF_BACKEND" output -raw app_secret_name)"
DB_JSON="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$DB_SECRET" --query SecretString --output text)"
APP_JSON="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$APP_SECRET" --query SecretString --output text 2>/dev/null || echo '{}')"

# Terraform creates the app secret's container but never its values -- anything
# it wrote would land in state in plaintext. So on a first run the secret is
# empty, and deploying that would ship the app a literal "null" JWT key and no
# OpenAI key: it would boot, then refuse every request. Stop here with the exact
# command instead. Terraform is idempotent, so re-running up.sh after populating
# it picks straight up from the fast path.
if [ -z "$(jq -r '.JWT_SECRET // empty' <<<"$APP_JSON")" ] \
   || [ -z "$(jq -r '.OPENAI_API_KEY // empty' <<<"$APP_JSON")" ]; then
  cat >&2 <<EOF

The application secret is not populated yet. Set it once, then re-run this script:

  aws secretsmanager put-secret-value --region $REGION --secret-id $APP_SECRET \\
    --secret-string '{"JWT_SECRET":"'"\$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"'","OPENAI_API_KEY":"sk-..."}'

EOF
  exit 1
fi

kubectl -n "$NAMESPACE" create secret generic safeshield-secrets \
  --from-literal=DATABASE_URL="$(jq -r .DATABASE_URL <<<"$DB_JSON")" \
  --from-literal=REDIS_URL="$(jq -r .REDIS_URL <<<"$DB_JSON")" \
  --from-literal=JWT_SECRET="$(jq -r .JWT_SECRET <<<"$APP_JSON")" \
  --from-literal=OPENAI_API_KEY="$(jq -r .OPENAI_API_KEY <<<"$APP_JSON")" \
  --dry-run=client -o yaml | kubectl apply -f -

# --- 5. Release --------------------------------------------------------------
step "Installing the chart (migrations run as a pre-install hook first)"
# fullnameOverride keeps resource names as safeshield-api / safeshield-worker
# rather than the default safeshield-safeshield-* (release name + chart name).
# Every reference below and in down.sh assumes the short form.
helm upgrade --install safeshield "$CHART" \
  --namespace "$NAMESPACE" \
  --set fullnameOverride=safeshield \
  --set image.repository="$ECR_URL" \
  --set image.tag="$IMAGE_TAG" \
  --set config.storage.bucket="$UPLOADS_BUCKET" \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="$APP_ROLE_ARN" \
  --set ingress.host="api.${SITE_URL#https://}" \
  --set "config.corsOrigins[0]=$SITE_URL" \
  --wait --timeout 10m

# --- 6. Data -----------------------------------------------------------------
step "Seeding the sample corpus (destroy wipes the database, so this runs every time)"
# Seeded in the worker, not the API. Extraction holds a whole PDF in memory and
# OOM-kills the 512Mi API pod; and six PDFs back to back in one process peak
# past even the worker's 1Gi (a single user upload stays under it, the bulk
# seed does not), so the ceiling is raised for this one-off. It is a limit, not
# a request, so the headroom costs nothing when unused, and the next `up.sh`
# resets it via helm.
kubectl -n "$NAMESPACE" set resources deploy/safeshield-worker --limits=memory=2Gi
kubectl -n "$NAMESPACE" rollout status deploy/safeshield-worker --timeout=3m
WORKER_POD="$(kubectl -n "$NAMESPACE" get pod -l app.kubernetes.io/component=worker \
  -o jsonpath='{.items[0].metadata.name}')"
# The sample PDFs live outside the Docker build context, so they are not in the
# image. Copy them into a writable path -- the root filesystem is read-only --
# and point the seed at it.
kubectl -n "$NAMESPACE" cp datasets "$WORKER_POD:/tmp/datasets"
kubectl -n "$NAMESPACE" exec "$WORKER_POD" -- python scripts/seed_corpus.py --datasets /tmp/datasets

# --- 7. Frontend build, pointed at this backend ------------------------------
step "Building and publishing the frontend"
ALB_HOST="$(kubectl -n "$NAMESPACE" get ingress safeshield \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
# npm ci first: on a fresh clone there is no node_modules, and any copied from
# a Windows checkout would hold Windows-only binaries (esbuild) that do not run
# in Linux. `ci` installs exactly the locked versions from scratch.
( cd frontend && npm ci && VITE_API_BASE_URL="https://${ALB_HOST}" npm run build )
aws s3 sync frontend/dist "s3://${FRONTEND_BUCKET}" --delete
# Invalidate index.html so the CDN serves the new bundle immediately. The
# fingerprinted assets get fresh URLs and do not need invalidating.
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/index.html" >/dev/null

step "Up."
echo "  Frontend: $SITE_URL"
echo "  API:      https://${ALB_HOST}/api/health"
echo
echo "  Tear it down when finished:  deploy/scripts/down.sh"
echo "  Leaving it running bills roughly \$0.28/hour."
