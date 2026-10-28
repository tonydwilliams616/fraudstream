# -----------------------------------------------------------------------------
# 00-account/versions.tf - versions, remote state and the AWS provider.
#
# What: stores this stack's state at fs-tfstate-831547420318/00-account/ and
#       pins the provider to the fraudstream-dev account.
# Why:  one state file per stack keeps blast radius small: a mistake in a
#       nightly-destroyed stack can never touch the budget or the data buckets.
# Stack type: PERSISTENT - never destroyed by the nightly teardown.
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
    key          = "00-account/terraform.tfstate"
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
      Stack      = "00-account"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
