# -----------------------------------------------------------------------------
# 25-dashboards/outputs.tf - where to find the dashboards.
# -----------------------------------------------------------------------------
output "dashboard_urls" {
  description = "Each dashboard's URL (sign in with IAM Identity Center)."
  value       = { for k, d in grafana_dashboard.this : k => d.url }
}
