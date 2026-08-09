# IRSA: the pod assumes a role, rather than carrying a key.
#
# The alternative is an access key in a Kubernetes Secret, which is a permanent
# credential that has to be rotated, tends to end up in a shell history, and
# grants the same access to anything that can read the Secret. IRSA issues a
# short-lived token scoped to one service account in one namespace.

data "aws_caller_identity" "current" {}

locals {
  namespace            = "safeshield"
  service_account_name = "safeshield"
  oidc_provider        = replace(module.eks.cluster_oidc_issuer_url, "https://", "")
}

data "aws_iam_policy_document" "app_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    # Both conditions matter. Without the `sub` check any service account in
    # the cluster could assume this role; without the `aud` check a token
    # minted for a different audience would be accepted.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:sub"
      values   = ["system:serviceaccount:${local.namespace}:${local.service_account_name}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "app_s3" {
  # Object-level access only, and only under the documents/ prefix. The
  # application never lists the bucket root, never deletes the bucket, and
  # never touches another prefix, so it is not granted the ability to.
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.uploads.arn}/documents/*"]
  }

  # ListBucket is bucket-level, hence a separate statement. Scoped by prefix so
  # it cannot enumerate anything else that later lands in this bucket.
  statement {
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.uploads.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["documents/*"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name}-app"
  assume_role_policy = data.aws_iam_policy_document.app_assume_role.json
  description        = "Assumed by the SafeShield pods through IRSA"
}

resource "aws_iam_role_policy" "app_s3" {
  name   = "${local.name}-s3"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_s3.json
}

# --- Application secrets -----------------------------------------------------

# The container is created here; the values are not. Putting a real secret in
# Terraform writes it to state in plaintext, where anyone with read access to
# the state bucket has it. Set the value once, out of band:
#
#   aws secretsmanager put-secret-value \
#     --secret-id safeshield-prod/app \
#     --secret-string '{"JWT_SECRET":"...","OPENAI_API_KEY":"..."}'
resource "aws_secretsmanager_secret" "app" {
  name        = "${local.name}/app"
  description = "JWT signing key and OpenAI API key. Values set out of band, never by Terraform."

  # Zero means immediate deletion on destroy. Left at the 30-day default, a
  # recreate fails with "secret already scheduled for deletion" and blocks a
  # rebuild of the environment.
  recovery_window_in_days = 0
}

# The database password *is* generated here, so it is already in state -- there
# is no way around that when Terraform creates the instance. Kept in Secrets
# Manager so the application reads it from one place and it can be rotated
# without a Terraform run.
resource "aws_secretsmanager_secret" "db" {
  name                    = "${local.name}/database"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    DATABASE_URL = "postgresql+psycopg://${aws_db_instance.main.username}:${random_password.db.result}@${aws_db_instance.main.endpoint}/${aws_db_instance.main.db_name}"
    REDIS_URL    = "redis://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
  })
}

# Lets External Secrets Operator, running as its own service account, pull both
# secrets into the Kubernetes Secret the Helm chart references.
data "aws_iam_policy_document" "external_secrets" {
  statement {
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.app.arn, aws_secretsmanager_secret.db.arn]
  }
}

data "aws_iam_policy_document" "external_secrets_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:sub"
      values   = ["system:serviceaccount:external-secrets:external-secrets"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "external_secrets" {
  name               = "${local.name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.external_secrets_assume_role.json
}

resource "aws_iam_role_policy" "external_secrets" {
  name   = "${local.name}-secrets-read"
  role   = aws_iam_role.external_secrets.id
  policy = data.aws_iam_policy_document.external_secrets.json
}
