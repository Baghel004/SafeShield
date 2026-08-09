# Frontend hosting, kept as a separate root module from the backend on purpose.
#
# The two have opposite lifecycles: this is meant to stay up permanently (it
# costs a dollar or two a month and is the link on a CV), while the EKS backend
# is applied on demand and destroyed after each session. One `terraform apply`
# covering both would couple a $200/month teardown to a static site that should
# never come down, so they are separate state files.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # backend "s3" {
  #   bucket       = "safeshield-tfstate"
  #   key          = "frontend/terraform.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "safeshield"
      Component = "frontend"
      ManagedBy = "terraform"
    }
  }
}

# CloudFront certificates must live in us-east-1 regardless of where everything
# else runs -- an ACM certificate in any other region is silently unusable by a
# distribution. This aliased provider exists only to create the cert there.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "safeshield"
      Component = "frontend"
      ManagedBy = "terraform"
    }
  }
}
