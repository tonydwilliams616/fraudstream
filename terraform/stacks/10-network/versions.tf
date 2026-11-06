# -----------------------------------------------------------------------------
# 10-network/versions.tf - versions, remote state and the AWS provider.
# Stack type: PERSISTENT - the VPC, subnets, routing and security groups cost
# nothing, and every other stack builds inside them, so they stay up between
# sessions. Hourly network costs (NAT gateways) live in 15-nat, which the
# nightly teardown destroys.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "fs-tfstate-831547420318"
    key          = "10-network/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  # Refuse to run against any other account, whatever credentials are active.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project    = "fraudstream"
      ManagedBy  = "terraform"
      Stack      = "10-network"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
