# -----------------------------------------------------------------------------
# 20-observability/variables.tf - inputs for the metrics and dashboard
# workspaces. Grafana admins are IAM Identity Center user IDs (not names):
# Managed Grafana signs people in through the organisation's Identity Center.
# The IDs come from the GitHub variable GRAFANA_ADMIN_USER_IDS (a JSON list,
# passed as TF_VAR_grafana_admin_user_ids) so they aren't kept in the repo.
# -----------------------------------------------------------------------------
variable "project_name" {
  description = "Short prefix used for naming resources (fs = fraudstream)."
  type        = string
  default     = "fs"
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "The fraudstream-dev account ID. The provider refuses to run against any other account."
  type        = string
  default     = "831547420318"
}

variable "grafana_version" {
  description = "Grafana version for the managed workspace."
  type        = string
  default     = "12.4"
}

variable "grafana_admin_user_ids" {
  description = "IAM Identity Center user IDs given the Grafana Admin role (each one is a billed editor licence)."
  type        = list(string)

  validation {
    condition     = length(var.grafana_admin_user_ids) >= 1
    error_message = "A Grafana workspace needs at least one admin."
  }
}
