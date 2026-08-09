#!/usr/bin/env bash
# Tear the backend down and prove nothing bill-generating is left.
#
# The frontend is deliberately untouched: it costs a dollar or two a month and
# is the permanent link. Only the on-demand backend comes down.
#
# The verification at the end is the point. The way an on-demand stack produces
# a surprise bill is not forgetting to run destroy -- it is a destroy that
# half-failed and left a NAT gateway or a load balancer running for days. So
# this does not trust `terraform destroy` reporting success; it asks AWS what
# is actually still there.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
CLUSTER="safeshield-prod"
NAMESPACE="safeshield"
TF_BACKEND="deploy/terraform"
TFVARS="${TFVARS:-ondemand.tfvars}"

cd "$(git rev-parse --show-toplevel)"
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

# --- 1. Remove the release first --------------------------------------------
# Helm created the ALB via the Ingress. If Terraform destroys the cluster while
# that load balancer still exists, the ALB and its security groups are orphaned
# -- owned by no Terraform state and billing quietly. Uninstalling first lets
# the controller delete the ALB it made.
step "Uninstalling the Helm release so the load balancer is cleaned up"
if aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER" 2>/dev/null; then
  helm uninstall safeshield -n "$NAMESPACE" --wait --timeout 5m || true
  # Give the load balancer controller a moment to actually delete the ALB
  # before the cluster it runs in is torn down.
  echo "Waiting for the load balancer to be released..."
  for _ in $(seq 1 30); do
    kubectl -n "$NAMESPACE" get ingress safeshield >/dev/null 2>&1 || break
    sleep 10
  done
else
  echo "Cluster already gone or unreachable; skipping."
fi

# --- 2. Destroy the infrastructure ------------------------------------------
step "Destroying infrastructure"
terraform -chdir="$TF_BACKEND" destroy -input=false -auto-approve -var-file="$TFVARS"

# --- 3. Verify, do not trust ------------------------------------------------
step "Verifying nothing bill-generating survived"
leftover=0
check() { # description, count-expression
  local n="$2"
  if [ "${n:-0}" -gt 0 ]; then
    printf '  \033[1;31mSTILL RUNNING\033[0m  %-22s %s\n' "$1" "$n"
    leftover=$((leftover + n))
  else
    printf '  ok            %-22s 0\n' "$1"
  fi
}

check "EKS clusters" "$(aws eks list-clusters --region "$REGION" \
  --query "length(clusters[?@=='$CLUSTER'])" --output text 2>/dev/null || echo 0)"
check "NAT gateways" "$(aws ec2 describe-nat-gateways --region "$REGION" \
  --filter Name=state,Values=available \
  --query "length(NatGateways[?contains(to_string(Tags), 'safeshield')])" --output text 2>/dev/null || echo 0)"
check "Load balancers" "$(aws elbv2 describe-load-balancers --region "$REGION" \
  --query "length(LoadBalancers[?contains(LoadBalancerName, 'safeshield') || contains(LoadBalancerName, 'k8s')])" --output text 2>/dev/null || echo 0)"
check "RDS instances" "$(aws rds describe-db-instances --region "$REGION" \
  --query "length(DBInstances[?DBInstanceIdentifier=='${CLUSTER}-postgres'])" --output text 2>/dev/null || echo 0)"
check "ElastiCache groups" "$(aws elasticache describe-replication-groups --region "$REGION" \
  --query "length(ReplicationGroups[?ReplicationGroupId=='${CLUSTER}-redis'])" --output text 2>/dev/null || echo 0)"
check "Running instances" "$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=instance-state-name,Values=running Name=tag:Project,Values=safeshield \
  --query "length(Reservations[].Instances[])" --output text 2>/dev/null || echo 0)"

echo
if [ "$leftover" -gt 0 ]; then
  echo "Some resources survived the destroy. They are billing. Investigate the" >&2
  echo "items marked STILL RUNNING above before walking away." >&2
  exit 1
fi
step "Down. Nothing hourly is left."
echo "  Still present, by design and near-free: the frontend (CDN), ECR images,"
echo "  the uploads bucket, and Secrets Manager entries. Destroy those by hand"
echo "  only if you are done with the project entirely."
