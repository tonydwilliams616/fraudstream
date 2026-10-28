# -----------------------------------------------------------------------------
# 05-registry/versions.tf - versions, remote state and the AWS provider.
# Stack type: PERSISTENT - images must survive the nightly teardown so a
# rebuild doesn't need a fresh image build.
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
    key          = "05-registry/terraform.tfstate"
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
      Stack      = "05-registry"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
