# -----------------------------------------------------------------------------
# 25-dashboards/main.tf - Grafana's data sources and dashboards, as code.
#
# What: a "fraudstream" folder, the two data sources (Managed Prometheus for the
#       consumer's metrics, CloudWatch for SageMaker, MSK and ECS) and the
#       dashboards in dashboards/*.json.
# Data sources: both sign their AWS requests with the Grafana workspace's own
#       IAM role (fs-grafana, 20-observability), which may query our one
#       Prometheus workspace and read CloudWatch. Their settings match what
#       Managed Grafana's own "AWS Data Sources" app creates (checked against
#       /api/datasources): the built-in Prometheus type with SigV4, and
#       ec2_iam_role meaning "the workspace's IAM role".
# Dashboards: each JSON file refers to the data source by its fixed uid
#       (fs-prometheus), so a rebuilt data source keeps every panel working.
#       Change a dashboard in the UI to try something, then export its JSON
#       into dashboards/ - the next apply overwrites UI-only changes.
# -----------------------------------------------------------------------------
locals {
  obs = data.terraform_remote_state.observability.outputs
}

resource "grafana_folder" "fraudstream" {
  title = "fraudstream"
  uid   = "fraudstream"
}

resource "grafana_data_source" "prometheus" {
  type       = "prometheus"
  name       = "fraudstream-prometheus"
  uid        = "fs-prometheus"
  url        = trimsuffix(local.obs.prometheus_query_url, "/")
  is_default = true

  json_data_encoded = jsonencode({
    httpMethod    = "POST"
    sigV4Auth     = true
    sigV4AuthType = var.prometheus_auth_type
    sigV4Region   = var.aws_region
  })
}

resource "grafana_data_source" "cloudwatch" {
  type = "cloudwatch"
  name = "fraudstream-cloudwatch"
  uid  = "fs-cloudwatch"

  json_data_encoded = jsonencode({
    authType      = var.cloudwatch_auth_type
    defaultRegion = var.aws_region
  })
}

resource "grafana_dashboard" "this" {
  for_each = fileset("${path.module}/dashboards", "*.json")

  folder      = grafana_folder.fraudstream.uid
  config_json = file("${path.module}/dashboards/${each.value}")
  overwrite   = true
}
