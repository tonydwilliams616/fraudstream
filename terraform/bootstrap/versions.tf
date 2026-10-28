# -----------------------------------------------------------------------------
# bootstrap/versions.tf - Terraform and provider versions, and the AWS provider.
#
# What: pins Terraform >= 1.10 and the AWS provider 6.x, and configures the
#       provider for the fraudstream-dev account.
# Why:  allowed_account_ids makes Terraform refuse to run against any other
#       account, whatever credentials are active in your shell - a cheap guard
#       against applying to clinical-trial-matching or kubemedic by mistake.
# Concept: "chicken and egg" state. This stack CREATES the state bucket, so its
#       first apply uses local state; backend.tf.example is then copied to
#       backend.tf and the state is migrated into the bucket (see README.md).
# Reused from clinical-trial-matching (terraform/bootstrap).
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10" # needed for S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
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
      Stack      = "bootstrap"
      Repository = "${var.github_owner}/${var.github_repo}"
    }
  }
}
