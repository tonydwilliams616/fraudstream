# -----------------------------------------------------------------------------
# 20-observability/alerting.tf - alert rules and where alerts go.
#
# What: uploads prometheus/rules.yml (SLO recording rules and
#       alerts, unit-tested in CI with promtool) to the Prometheus workspace,
#       and configures the workspace's Alertmanager to send firing AND resolved
#       alerts to the ops-alerts SNS topic (00-account), which emails you.
# Routing: one receiver; alerts are grouped by name, re-sent every 4 hours
#       while still firing, and a "resolved" message follows when they clear.
# Permissions: the topic's policy and its KMS key (00-account) allow Managed
#       Prometheus to publish - no IAM role is involved.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "account" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "00-account/terraform.tfstate"
    region = var.aws_region
  }
}

resource "aws_prometheus_rule_group_namespace" "fraudstream" {
  name         = "fraudstream"
  workspace_id = aws_prometheus_workspace.this.id
  data         = file("${path.module}/prometheus/rules.yml")
}

resource "aws_prometheus_alert_manager_definition" "this" {
  workspace_id = aws_prometheus_workspace.this.id
  definition   = <<-EOT
    alertmanager_config: |
      route:
        receiver: ops-email
        group_by: ['alertname']
        group_wait: 30s
        group_interval: 5m
        repeat_interval: 4h
      receivers:
        - name: ops-email
          sns_configs:
            - topic_arn: ${data.terraform_remote_state.account.outputs.ops_alerts_topic_arn}
              send_resolved: true
              subject: 'fraudstream {{ .Status | toUpper }}: {{ .CommonLabels.alertname }}'
              sigv4:
                region: ${var.aws_region}
  EOT
}
