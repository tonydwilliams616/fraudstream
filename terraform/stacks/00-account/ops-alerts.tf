# -----------------------------------------------------------------------------
# 00-account/ops-alerts.tf - the "something is unhealthy" email channel.
#
# What: an SNS topic, emailed to you, for operational alerts: consumer lag,
#       endpoint errors, drift alarms, failed deploys (from Phase 2 onwards).
# Why:  kept separate from budget alerts so "the platform is unhealthy" and
#       "money is being spent" stay distinct. Fraud alerts do NOT go here - at
#       ~0.5% fraud and 10 transactions/second that would be ~3 emails a minute;
#       they go to the fraud.alerts Kafka topic instead.
# Encryption: a customer-managed KMS key, not the AWS-managed SNS key. Services
#       that publish to an encrypted topic need to use its key, and the
#       AWS-managed key's policy can't be changed. Ours lets two services in:
#       Managed Prometheus' Alertmanager (20-observability) and CloudWatch
#       alarms (e.g. MSK consumer lag in 35-apps). ~$1/month, rotated yearly.
# Who may publish: this account (the default), plus Managed Prometheus - but
#       only from a workspace in this account (confused-deputy protection).
# Persistent, so it survives teardown.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "ops_alerts_key" {
  # A key policy always names the key itself as "*"; the account statement is
  # AWS's standard default that keeps the key manageable through IAM.
  #checkov:skip=CKV_AWS_109:KMS key policies must use Resource "*" (it means this key); the account-root statement is AWS's default
  #checkov:skip=CKV_AWS_111:KMS key policies must use Resource "*" (it means this key); the account-root statement is AWS's default
  #checkov:skip=CKV_AWS_356:KMS key policies must use Resource "*" (it means this key)
  statement {
    sid       = "AccountAdministersKey"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.aws_account_id}:root"]
    }
  }

  statement {
    sid       = "ServicesEncryptForTopic"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["aps.amazonaws.com", "cloudwatch.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "ops_alerts" {
  description         = "Encrypts the ops-alerts SNS topic; usable by Managed Prometheus and CloudWatch alarms."
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.ops_alerts_key.json
}

resource "aws_kms_alias" "ops_alerts" {
  name          = "alias/${var.project_name}-ops-alerts"
  target_key_id = aws_kms_key.ops_alerts.key_id
}

resource "aws_sns_topic" "ops_alerts" {
  name              = "${var.project_name}-ops-alerts"
  kms_master_key_id = aws_kms_key.ops_alerts.arn
}

data "aws_iam_policy_document" "ops_alerts_topic" {
  # The default topic policy: anything in this account may use the topic.
  statement {
    sid = "AccountUsesTopic"
    actions = [
      "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:AddPermission",
      "sns:RemovePermission", "sns:DeleteTopic", "sns:Subscribe",
      "sns:ListSubscriptionsByTopic", "sns:Publish",
    ]
    resources = [aws_sns_topic.ops_alerts.arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceOwner"
      values   = [var.aws_account_id]
    }
  }

  # Alertmanager in Managed Prometheus - from a workspace in this account only.
  statement {
    sid       = "PrometheusAlertmanagerPublishes"
    actions   = ["sns:Publish", "sns:GetTopicAttributes"]
    resources = [aws_sns_topic.ops_alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["aps.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:aps:${var.aws_region}:${var.aws_account_id}:workspace/*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }

  # CloudWatch alarms in this account.
  statement {
    sid       = "CloudWatchAlarmsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_sns_topic_policy" "ops_alerts" {
  #checkov:skip=CKV_AWS_169:Principal "*" is limited by AWS:SourceOwner to this account - the SNS default statement
  arn    = aws_sns_topic.ops_alerts.arn
  policy = data.aws_iam_policy_document.ops_alerts_topic.json
}

# Requires clicking the confirmation link AWS emails after the first apply.
resource "aws_sns_topic_subscription" "ops_alerts_email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
