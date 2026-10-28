# -----------------------------------------------------------------------------
# 00-account/variables.tf - inputs for the account guardrails stack.
#
# What: names, the account, the alert email, the budget and the anomaly switch.
# Why:  alert_email is marked sensitive and comes from the GitHub secret
#       ALERT_EMAIL (TF_VAR_alert_email in the workflows). Plans are posted as
#       PR comments, and GitHub's secret masking doesn't apply to those, so
#       "sensitive" is what keeps your address out of them.
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

variable "alert_email" {
  description = "Email address for budget, anomaly and ops alerts. Set via TF_VAR_alert_email (GitHub secret ALERT_EMAIL)."
  type        = string
  sensitive   = true
}

variable "monthly_budget_limit" {
  description = "Account-wide monthly budget in USD (~10 sessions on MSK Serverless plus headroom)."
  type        = number
  default     = 50
}

variable "enable_anomaly_detection" {
  description = <<-EOT
    Create our own Cost Anomaly Detection monitor. AWS allows only one SERVICE
    monitor per account and may already have created a default one when Cost
    Explorer was enabled. Check with:
      aws ce get-anomaly-monitors --query 'AnomalyMonitors[].MonitorName'
    Empty list -> set true. A default monitor exists -> leave false (it already
    watches every service; you just won't get our $10 email threshold).
  EOT
  type        = bool
  default     = true # checked 28 Oct 2026: no existing monitor in fraudstream-dev
}
