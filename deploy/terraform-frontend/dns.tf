# Everything here is created only when a custom domain is configured. With the
# default *.cloudfront.net domain there is no certificate to issue and no DNS
# to manage, so the whole file is a no-op -- which is the intended path for a
# demo.

resource "aws_acm_certificate" "site" {
  count = local.use_custom_domain ? 1 : 0

  provider          = aws.us_east_1 # CloudFront only accepts certs from here
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.use_custom_domain ? {
    for dvo in aws_acm_certificate.site[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = var.hosted_zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60

  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  count = local.use_custom_domain ? 1 : 0

  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# Points the domain at CloudFront. An alias A record, not a CNAME: the zone apex
# cannot hold a CNAME, and an alias is free where a CNAME lookup is billed.
resource "aws_route53_record" "site" {
  count = local.use_custom_domain ? 1 : 0

  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}
