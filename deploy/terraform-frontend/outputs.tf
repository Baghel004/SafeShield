output "bucket_name" {
  description = "Sync the built frontend here: aws s3 sync frontend/dist s3://<bucket>"
  value       = aws_s3_bucket.site.id
}

output "distribution_id" {
  description = "Needed to invalidate the cache after a deploy so index.html is refetched."
  value       = aws_cloudfront_distribution.site.id
}

output "site_url" {
  description = "Where the app is served. This exact origin must be in the backend's CORS_ORIGINS."
  value       = "https://${local.site_domain}"
}

output "cors_origin" {
  description = "Pass to Helm as config.corsOrigins[0]."
  value       = "https://${local.site_domain}"
}
