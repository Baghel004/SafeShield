terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state with locking. Local state on one laptop means a second person
  # applying destroys what the first created, and a lost laptop loses the only
  # record of what exists. Bootstrap the bucket and table once, by hand, before
  # the first apply -- Terraform cannot create its own backend.
  #
  # backend "s3" {
  #   bucket       = "safeshield-tfstate"
  #   key          = "prod/terraform.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "safeshield"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
