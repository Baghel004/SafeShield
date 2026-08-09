output "cluster_name" {
  value = module.eks.cluster_name
}

output "configure_kubectl" {
  description = "Run this before helm install."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "uploads_bucket" {
  value = aws_s3_bucket.uploads.id
}

output "app_role_arn" {
  description = "Set as serviceAccount.annotations['eks.amazonaws.com/role-arn'] in Helm values."
  value       = aws_iam_role.app.arn
}

output "external_secrets_role_arn" {
  value = aws_iam_role.external_secrets.arn
}

output "database_secret_name" {
  description = "Secrets Manager entry holding DATABASE_URL and REDIS_URL."
  value       = aws_secretsmanager_secret.db.name
}

output "app_secret_name" {
  description = "Secrets Manager entry for JWT_SECRET and OPENAI_API_KEY. Populate it manually."
  value       = aws_secretsmanager_secret.app.name
}

# Deliberately not output: the database password and the connection string. An
# output lands in state and prints on every apply; anything needing the URL
# reads it from Secrets Manager instead.

output "helm_values" {
  description = "The values that depend on infrastructure. Pass with --set or write to a values file."
  value = {
    "image.repository"                                          = aws_ecr_repository.api.repository_url
    "config.storage.bucket"                                     = aws_s3_bucket.uploads.id
    "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn" = aws_iam_role.app.arn
  }
}
