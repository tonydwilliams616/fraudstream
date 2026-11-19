# -----------------------------------------------------------------------------
# 20-observability/grafana-terraform.tf - lets Terraform manage what's INSIDE
# Grafana (data sources, folders, dashboards) from 25-dashboards.
#
# What: a Grafana service account with the Admin role, and a token for it.
#       25-dashboards reads the token from this stack's state and gives it to
#       the Grafana Terraform provider.
# Why a token at all: the AWS API creates the workspace, but data sources and
#       dashboards live in Grafana's own API, which needs a Grafana credential.
#       People still sign in with SSO; only Terraform uses this token.
# Rotation: Managed Grafana tokens live at most 30 days. time_rotating marks
#       the token for replacement after 25 days, and every rebuild (stack =
#       all) applies this stack before 25-dashboards, so the token is renewed
#       before it's needed. If 25-dashboards ever fails with 401 Unauthorized,
#       apply this stack first.
# Where the secret lives: in this stack's state (encrypted S3, readable only by
#       the Terraform roles) and in a sensitive output - never in the repo.
#       A billed API user ($9/month for Admin) only in months it's used.
# -----------------------------------------------------------------------------
resource "aws_grafana_workspace_service_account" "terraform" {
  name         = "terraform"
  grafana_role = "ADMIN"
  workspace_id = aws_grafana_workspace.this.id
}

resource "time_rotating" "grafana_token" {
  rotation_days = 25
}

resource "aws_grafana_workspace_service_account_token" "terraform" {
  name               = "terraform-${time_rotating.grafana_token.unix}"
  service_account_id = aws_grafana_workspace_service_account.terraform.service_account_id
  seconds_to_live    = 2592000 # 30 days, the maximum
  workspace_id       = aws_grafana_workspace.this.id

  lifecycle {
    replace_triggered_by = [time_rotating.grafana_token]
  }
}

output "grafana_terraform_token" {
  description = "Token for the Grafana Terraform provider (25-dashboards)."
  value       = aws_grafana_workspace_service_account_token.terraform.key
  sensitive   = true
}
