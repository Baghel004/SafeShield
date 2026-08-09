locals {
  name              = var.project
  use_custom_domain = var.domain_name != ""
  # The origin the browser will be served from. The backend needs this exact
  # value in CORS_ORIGINS, and it is printed as an output for that reason.
  site_domain = local.use_custom_domain ? var.domain_name : aws_cloudfront_distribution.site.domain_name
}

# --- Bucket ------------------------------------------------------------------

resource "aws_s3_bucket" "site" {
  bucket = "${local.name}-frontend"
}

# The bucket is private. CloudFront reaches it through Origin Access Control, so
# nothing is served from S3 directly -- a bucket policy is the only thing that
# can read it, and that policy names one distribution. A public bucket would
# bypass the CDN, its caching and its TLS entirely.
resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "site" {
  statement {
    sid       = "AllowCloudFrontRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # Scoped to this one distribution: without the condition, any CloudFront
    # distribution in any AWS account could read the bucket.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
}

# --- CloudFront --------------------------------------------------------------

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${local.name}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = var.price_class
  comment             = "SafeShield frontend"

  aliases = local.use_custom_domain ? [var.domain_name] : []

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # AWS managed CachingOptimized. Cache key is the path alone, which is
    # correct for a static bundle with fingerprinted filenames.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  # SPA routing. /chat and /documents are React Router paths, not files in the
  # bucket, so S3 returns 403 (not 404 -- ListBucket is denied). Both are
  # rewritten to index.html so a reload or a shared deep link resolves instead
  # of showing CloudFront's error page.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # The default *.cloudfront.net certificate when there is no custom domain;
    # the ACM cert otherwise. cloudfront_default_certificate and
    # acm_certificate_arn are mutually exclusive, hence the conditional.
    cloudfront_default_certificate = local.use_custom_domain ? null : true
    acm_certificate_arn            = local.use_custom_domain ? aws_acm_certificate_validation.site[0].certificate_arn : null
    ssl_support_method             = local.use_custom_domain ? "sni-only" : null
    minimum_protocol_version       = local.use_custom_domain ? "TLSv1.2_2021" : "TLSv1"
  }
}
