locals {
  name = "${var.project}-${var.environment}"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.16"

  name = local.name
  cidr = var.vpc_cidr
  azs  = var.azs

  # Three tiers. The database and cache sit in subnets with no route to the
  # internet at all -- not merely "private" behind a NAT gateway, but with no
  # egress route, so a compromised node cannot exfiltrate from them outbound.
  public_subnets   = [for i, _ in var.azs : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets  = [for i, _ in var.azs : cidrsubnet(var.vpc_cidr, 8, i + 10)]
  database_subnets = [for i, _ in var.azs : cidrsubnet(var.vpc_cidr, 8, i + 20)]

  enable_nat_gateway   = true
  single_nat_gateway   = var.single_nat_gateway
  enable_dns_hostnames = true
  enable_dns_support   = true

  create_database_subnet_group = true

  # Required by the AWS Load Balancer Controller to discover where to place
  # load balancers. Without these tags an Ingress is created and never gets an
  # address, with no error anywhere obvious.
  public_subnet_tags = {
    "kubernetes.io/role/elb"              = "1"
    "kubernetes.io/cluster/${local.name}" = "shared"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"     = "1"
    "kubernetes.io/cluster/${local.name}" = "shared"
  }
}

# S3 traffic from the nodes stays on the AWS network rather than going out
# through the NAT gateway. Uploads are megabytes each and NAT is billed per
# gigabyte, so this is both cheaper and one less hop.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = { Name = "${local.name}-s3" }
}
