# -----------------------------------------------------------------------------
# 00-account/ops-alerts.tf - the "something is unhealthy" email channel.
#
# What: an SNS topic, emailed to you, for operational alerts: consumer lag,
#       endpoint errors, drift alarms, failed deploys (from Phase 2 onwards).
# Why:  kept separate from budget alerts so "the platform is unhealthy" and
#       "money is being spent" stay distinct. Fraud alerts do NOT go here - at
#       ~0.5% fraud and 10 transactions/second that would be ~3 emails a minute;
#       they go to the fraud.alerts Kafka topic instead.
# Encrypted with the AWS-managed SNS key; CloudWatch alarms in the same account
# can publish to it. Persistent and pennies a month, so it survives teardown.
# Reused pattern from clinical-trial-matching (00-account/ops-alerts.tf).
# -----------------------------------------------------------------------------
resource "aws_sns_topic" "ops_alerts" {
  name              = "${var.project_name}-ops-alerts"
  kms_master_key_id = "alias/aws/sns"
}

# Requires clicking the confirmation link AWS emails after the first apply.
resource "aws_sns_topic_subscription" "ops_alerts_email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
