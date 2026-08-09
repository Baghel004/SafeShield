variable "region" {
  description = "Region for the S3 bucket. CloudFront is global; the ACM cert is pinned to us-east-1 regardless."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  type    = string
  default = "safeshield"
}

variable "domain_name" {
  description = <<-EOT
    Custom domain for the site, e.g. app.safeshield.example. Leave empty to use
    the CloudFront *.cloudfront.net domain, which needs no DNS and no certificate
    -- the right choice for a demo. Set it only if you own a domain and have a
    Route 53 hosted zone.
  EOT
  type        = string
  default     = ""
}

variable "hosted_zone_id" {
  description = "Route 53 zone id for domain_name. Required only when domain_name is set."
  type        = string
  default     = ""
}

variable "price_class" {
  description = "PriceClass_100 is North America + Europe edges only -- the cheapest, and plenty for a demo. PriceClass_All adds Asia and South America."
  type        = string
  default     = "PriceClass_100"
}
