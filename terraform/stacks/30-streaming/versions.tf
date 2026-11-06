# -----------------------------------------------------------------------------
# 30-streaming/versions.tf - versions, remote state and the AWS provider.
# Stack type: EPHEMERAL - MSK brokers bill by the hour, so this stack is in
# EPHEMERAL_STACKS and the nightly teardown destroys it. Topic data does not
# survive a teardown; S3 (the scored archive) is the system of record.
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
    key          = "30-streaming/terraform.tfstate"
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
      Stack      = "30-streaming"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
