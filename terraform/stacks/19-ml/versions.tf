# -----------------------------------------------------------------------------
# 19-ml/versions.tf - versions, remote state and the AWS provider.
# Stack type: PERSISTENT - IAM roles and the model registry cost nothing and
# must outlive teardown: the registry holds the history of every approved
# model, and the ephemeral stacks attach these roles each time they're rebuilt.
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
    key          = "19-ml/terraform.tfstate"
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
      Stack      = "19-ml"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
