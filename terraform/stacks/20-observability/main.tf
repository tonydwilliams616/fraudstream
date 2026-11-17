# -----------------------------------------------------------------------------
# 20-observability/main.tf - Amazon Managed Service for Prometheus (AMP).
#
# What: the workspace where the consumer's metrics are stored and queried
#       (throughput, end-to-end latency, consumer lag, alert rate, scores).
#       A collector next to the consumer (Phase 5, in 35-apps) scrapes the
#       consumer's /metrics endpoint and remote-writes samples here, signed
#       with the task's IAM role. Grafana reads them back with its own role.
# Why managed: no Prometheus server, disk or retention to run; billed per
#       sample ingested and queried (our volume sits in the free tier), and
#       nothing hourly - so it can stay up between sessions.
# Alerting rules and SLO burn-rate rules are added to this workspace later.
# -----------------------------------------------------------------------------
resource "aws_prometheus_workspace" "this" {
  alias = "${var.project_name}-metrics"
}
