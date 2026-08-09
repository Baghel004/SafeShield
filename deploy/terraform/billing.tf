# A backstop, not the primary cost control.
#
# For an on-demand stack the real defence against a surprise bill is a clean
# `terraform destroy` and the teardown check that nothing bill-generating was
# left behind -- billing metrics lag by hours, so an alarm would fire long
# after the money was spent. This exists to catch the case that defence misses:
# a destroy that partially failed and left a NAT gateway or a node group
# quietly running for days.

# Billing metrics are published only in us-east-1, regardless of where the
# resources actually are. An alarm built against any other region's provider
# finds no data and never fires.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "aws_sns_topic" "billing" {
  count = var.billing_alarm_email == "" ? 0 : 1

  provider = aws.us_east_1
  name     = "${local.name}-billing"
}

resource "aws_sns_topic_subscription" "billing" {
  count = var.billing_alarm_email == "" ? 0 : 1

  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.billing[0].arn
  protocol  = "email"
  endpoint  = var.billing_alarm_email
  # Confirm the subscription from the email AWS sends, or the alarm has nowhere
  # to deliver.
}

resource "aws_cloudwatch_metric_alarm" "billing" {
  count = var.billing_alarm_email == "" ? 0 : 1

  provider            = aws.us_east_1
  alarm_name          = "${local.name}-estimated-charges"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600 # 6h -- the fastest billing metrics update
  statistic           = "Maximum"
  threshold           = var.billing_alarm_threshold_usd
  alarm_description   = "Estimated AWS charges crossed $${var.billing_alarm_threshold_usd}. For an on-demand stack this usually means a teardown did not complete."
  alarm_actions       = [aws_sns_topic.billing[0].arn]

  dimensions = {
    Currency = "USD"
  }
}
