# -----------------------------------------------------------------------------
# 25-dashboards/versions.tf - versions, remote state, and the AWS and Grafana
# providers.
# Stack type: PERSISTENT - data sources and dashboards cost nothing and live in
# the persistent Grafana workspace (20-observability); rebuilding them nightly
# would only lose anything changed in the UI between sessions.
# The Grafana provider talks to Grafana's own API with the service account
# token that 20-observability creates and rotates.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    grafana = {
      source  = "grafana/grafana"
      version = ">= 3.0, < 5.0"
    }
  }

  backend "s3" {
    bucket       = "fs-tfstate-831547420318"
    key          = "25-dashboards/terraform.tfstate"
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
      Stack      = "25-dashboards"
      Repository = "tonydwilliams616/fraudstream"
    }
  }
}

data "terraform_remote_state" "observability" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "20-observability/terraform.tfstate"
    region = var.aws_region
  }
}

provider "grafana" {
  url  = data.terraform_remote_state.observability.outputs.grafana_url
  auth = data.terraform_remote_state.observability.outputs.grafana_terraform_token
}
