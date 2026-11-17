# -----------------------------------------------------------------------------
# 20-observability/outputs.tf - read by later stacks via terraform_remote_state
# (35-apps' metrics collector remote-writes to the Prometheus workspace).
# -----------------------------------------------------------------------------
output "prometheus_workspace_id" {
  description = "AMP workspace ID."
  value       = aws_prometheus_workspace.this.id
}

output "prometheus_workspace_arn" {
  description = "AMP workspace ARN (for IAM policies that write or query metrics)."
  value       = aws_prometheus_workspace.this.arn
}

output "prometheus_remote_write_url" {
  description = "Where the collector sends samples."
  value       = "${aws_prometheus_workspace.this.prometheus_endpoint}api/v1/remote_write"
}

output "prometheus_query_url" {
  description = "Prometheus-compatible query endpoint (Grafana's data source URL)."
  value       = aws_prometheus_workspace.this.prometheus_endpoint
}

output "grafana_url" {
  description = "Sign in here with IAM Identity Center."
  value       = "https://${aws_grafana_workspace.this.endpoint}"
}

output "grafana_workspace_id" {
  description = "Managed Grafana workspace ID."
  value       = aws_grafana_workspace.this.id
}
