# -----------------------------------------------------------------------------
# 18-data/versions.tf - versions, remote state and the AWS provider.
# Stack type: PERSISTENT - holds datasets, model artefacts and the scored
# archive, which must survive every nightly teardown (and feed DR in Phase 7).
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
    key          = "18-data/terraform.tfstate"
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
      Stack      = "18-data"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
