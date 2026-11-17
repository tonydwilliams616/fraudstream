# -----------------------------------------------------------------------------
# 20-observability/grafana.tf - Amazon Managed Grafana for dashboards.
#
# What: a Grafana workspace, signed in to with IAM Identity Center (SSO), that
#       reads Prometheus (AMP) and CloudWatch (SageMaker endpoint, MSK, ECS
#       metrics) through its own IAM role. Dashboards are added as code later.
# Why customer-managed role: the permissions are in this file and reviewed in
#       a PR, instead of a role AWS creates outside Terraform.
# Cost: $9 per active admin/editor per month (a workspace needs at least one),
#       after a 90-day free trial. No hourly charge.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "grafana_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["grafana.amazonaws.com"]
    }

    # Only Grafana workspaces in this account may use the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "grafana" {
  name               = "${var.project_name}-grafana"
  description        = "Lets the fraudstream Grafana workspace read Prometheus and CloudWatch metrics."
  assume_role_policy = data.aws_iam_policy_document.grafana_trust.json
}

data "aws_iam_policy_document" "grafana_read" {
  statement {
    sid = "QueryOurPrometheus"
    actions = [
      "aps:QueryMetrics",
      "aps:GetSeries",
      "aps:GetLabels",
      "aps:GetMetricMetadata",
      "aps:DescribeWorkspace",
    ]
    resources = [aws_prometheus_workspace.this.arn]
  }

  # CloudWatch has no resource-level permissions for these read and list
  # calls, so they can only be granted on "*". All are read-only.
  #checkov:skip=CKV_AWS_356:CloudWatch metric/alarm read and list actions don't support resource-level permissions
  statement {
    sid = "ReadCloudWatch"
    actions = [
      "aps:ListWorkspaces",
      "cloudwatch:DescribeAlarmsForMetric",
      "cloudwatch:DescribeAlarmHistory",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListMetrics",
      "cloudwatch:GetMetricData",
      "cloudwatch:GetInsightRuleReport",
      "ec2:DescribeRegions",
      "tag:GetResources",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "grafana_read" {
  name   = "read-metrics"
  role   = aws_iam_role.grafana.id
  policy = data.aws_iam_policy_document.grafana_read.json
}

resource "aws_grafana_workspace" "this" {
  name                     = "${var.project_name}-grafana"
  description              = "fraudstream dashboards: stream throughput and lag, scoring latency, fraud rate, endpoint health."
  grafana_version          = var.grafana_version
  account_access_type      = "CURRENT_ACCOUNT"
  authentication_providers = ["AWS_SSO"]
  permission_type          = "CUSTOMER_MANAGED"
  role_arn                 = aws_iam_role.grafana.arn
  data_sources             = ["PROMETHEUS", "CLOUDWATCH"]
}

resource "aws_grafana_role_association" "admins" {
  workspace_id = aws_grafana_workspace.this.id
  role         = "ADMIN"
  user_ids     = var.grafana_admin_user_ids
}
