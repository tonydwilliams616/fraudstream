# -----------------------------------------------------------------------------
# modules/cost-guardrails/outputs.tf - what the calling stack can use.
# Reused verbatim from clinical-trial-matching.
# -----------------------------------------------------------------------------
output "sns_topic_arn" {
  description = "SNS topic receiving budget alerts - subscribe Lambda/Slack/Chatbot here later."
  value       = aws_sns_topic.budget_alerts.arn
}

output "account_budget_name" {
  value = aws_budgets_budget.account_monthly.name
}

output "project_budget_name" {
  value = try(aws_budgets_budget.project_monthly[0].name, null)
}
