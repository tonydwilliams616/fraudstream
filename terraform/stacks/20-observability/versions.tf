# -----------------------------------------------------------------------------
# 20-observability/versions.tf - versions, remote state and the AWS provider.
# Stack type: PERSISTENT - the Prometheus and Grafana workspaces cost nothing
# while idle (Prometheus bills per sample; Grafana per active user per month),
# and the Grafana workspace carries the SSO sign-in and the URL people use, so
# rebuilding it nightly would save nothing and break both.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13" # rotates the Grafana token (grafana-terraform.tf)
    }
  }

  backend "s3" {
    bucket       = "fs-tfstate-831547420318"
    key          = "20-observability/terraform.tfstate"
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
      Stack      = "20-observability"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}
