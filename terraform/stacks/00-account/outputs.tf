# -----------------------------------------------------------------------------
# 00-account/outputs.tf - read by later stacks via terraform_remote_state
# (e.g. 40-serving's CloudWatch alarms publish to the ops-alerts topic).
# -----------------------------------------------------------------------------
output "budget_alerts_topic_arn" {
  description = "SNS topic receiving budget alerts."
  value       = module.cost_guardrails.sns_topic_arn
}

output "account_budget_name" {
  description = "Account-wide monthly budget."
  value       = module.cost_guardrails.account_budget_name
}

output "ops_alerts_topic_arn" {
  description = "SNS topic for operational alerts (alarms, drift, failed deploys)."
  value       = aws_sns_topic.ops_alerts.arn
}
