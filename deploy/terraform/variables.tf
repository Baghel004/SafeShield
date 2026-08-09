variable "region" {
  description = "AWS region."
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Environment name, used in resource names and tags."
  type        = string
  default     = "prod"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "safeshield"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "azs" {
  description = "Availability zones. Two is the minimum RDS will accept for a subnet group."
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b"]
}

variable "kubernetes_version" {
  type    = string
  default = "1.31"
}

variable "node_instance_types" {
  description = "Node group instance types. t3.medium fits the API, worker and a Prometheus stack with room to spare."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro is the cheapest that supports pgvector at this scale."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "single_nat_gateway" {
  description = <<-EOT
    One NAT gateway instead of one per AZ. Saves roughly $32/month per gateway
    and makes that AZ a single point of failure for outbound traffic. Correct
    for a demo, wrong for anything with an availability target.
  EOT
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = <<-EOT
    Blocks `terraform destroy` from dropping the database, and forces a final
    snapshot. Correct for a permanent deployment; wrong for an on-demand one,
    where it leaves RDS running after a destroy you thought completed. Set false
    via ondemand.tfvars for the tear-down-after-each-session workflow.
  EOT
  type        = bool
  default     = true
}

variable "billing_alarm_email" {
  description = "Where to send the estimated-charges alarm. Empty disables the alarm and its SNS topic entirely."
  type        = string
  default     = ""
}

variable "billing_alarm_threshold_usd" {
  description = "Dollar figure that trips the billing alarm. For an on-demand stack this should sit above one session's cost and below a month of it left running by accident."
  type        = number
  default     = 20
}
