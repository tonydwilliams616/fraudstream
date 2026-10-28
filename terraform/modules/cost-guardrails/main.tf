# -----------------------------------------------------------------------------
# modules/cost-guardrails/main.tf - budgets and spend alerts for an account.
#
# What: an SNS topic + email subscription, an account-wide monthly budget with
#       percentage alerts, an optional tag-scoped project budget, and an
#       optional Cost Anomaly Detection monitor.
# Why:  the first thing applied in any account that can spend money, so a
#       forgotten MSK cluster or endpoint gets noticed in hours, not at the end
#       of the month. Budgets fire on ACTUAL and FORECASTED spend.
# Concept: budgets react to thresholds you choose; anomaly detection uses ML on
#       your spend history to flag unusual jumps you didn't predict.
# Reused verbatim from clinical-trial-matching (terraform/modules/cost-guardrails).
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# SNS topic for budget alerts (lets you hook up Slack/Lambda automation later)
# -----------------------------------------------------------------------------
resource "aws_sns_topic" "budget_alerts" {
  #checkov:skip=CKV_AWS_26:AWS Budgets cannot publish to a topic encrypted with the AWS-managed SNS key; alerts carry no sensitive data
  name = "${var.project_name}-budget-alerts"
  tags = var.tags
}

data "aws_iam_policy_document" "budget_alerts" {
  statement {
    sid       = "AllowBudgetsPublish"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.budget_alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_alerts.json
}

# Requires clicking the confirmation link AWS emails you after apply.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# -----------------------------------------------------------------------------
# Account-wide monthly budget
# -----------------------------------------------------------------------------
resource "aws_budgets_budget" "account_monthly" {
  name         = "${var.project_name}-account-monthly"
  budget_type  = "COST"
  limit_amount = format("%.1f", var.monthly_budget_limit) # AWS stores "50.0"; avoids perpetual diffs
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.alert_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
      subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
    }
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}

# -----------------------------------------------------------------------------
# Project-scoped budget (filters on a cost allocation tag, e.g. Project=ctm)
# The tag must be ACTIVATED as a cost allocation tag in Billing for this to work.
# -----------------------------------------------------------------------------
resource "aws_ce_cost_allocation_tag" "project" {
  count   = var.activate_cost_allocation_tag ? 1 : 0
  tag_key = var.project_tag_key
  status  = "Active"
}

resource "aws_budgets_budget" "project_monthly" {
  count = var.project_tag_value == null ? 0 : 1

  name         = "${var.project_name}-project-monthly"
  budget_type  = "COST"
  limit_amount = format("%.1f", var.project_budget_limit)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:%s$%s", var.project_tag_key, var.project_tag_value)]
  }

  dynamic "notification" {
    for_each = var.alert_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
      subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts.arn]
    }
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}

# -----------------------------------------------------------------------------
# Cost Anomaly Detection - catches sudden spikes (e.g. GPU nodes not scaling down)
# Note: AWS allows only ONE dimensional SERVICE monitor per account. If you
# already have one (AWS sometimes creates a default), set enable_anomaly_detection = false.
# -----------------------------------------------------------------------------
resource "aws_ce_anomaly_monitor" "services" {
  count             = var.enable_anomaly_detection ? 1 : 0
  name              = "${var.project_name}-service-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "email" {
  count            = var.enable_anomaly_detection ? 1 : 0
  name             = "${var.project_name}-anomaly-alerts"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services[0].arn]

  subscriber {
    type    = "EMAIL"
    address = var.alert_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.anomaly_threshold_usd)]
    }
  }
}
