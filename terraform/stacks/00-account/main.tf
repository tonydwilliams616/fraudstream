# -----------------------------------------------------------------------------
# 00-account/main.tf - account guardrails, applied before anything that spends.
#
# What: the cost-guardrails module with an account-wide $50/month budget.
# Why:  fraudstream-dev is a dedicated account, so the account's whole bill IS
#       the project's cost. That's why there's no tag-scoped project budget here
#       (project_tag_value = null) - unlike clinical-trial-matching, which needed
#       one, and whose cost allocation tags can only be activated from the
#       management account anyway.
# Stack type: PERSISTENT. Cost: budgets are free (first two per account), SNS
#       pennies.
# -----------------------------------------------------------------------------
module "cost_guardrails" {
  source = "../../modules/cost-guardrails"

  project_name = var.project_name
  alert_email  = var.alert_email

  monthly_budget_limit = var.monthly_budget_limit
  project_tag_value    = null # dedicated account: the account budget is the project budget

  activate_cost_allocation_tag = false # only possible from the management account

  enable_anomaly_detection = var.enable_anomaly_detection
  anomaly_threshold_usd    = 10
}
