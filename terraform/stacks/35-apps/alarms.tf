# -----------------------------------------------------------------------------
# 35-apps/alarms.tf - "is the consumer keeping up?" from outside the consumer.
#
# What: a CloudWatch alarm on MSK's own consumer-lag metric for our consumer
#       group: the estimated time (seconds) to drain the most-behind partition
#       of `transactions`. Alarm and recovery both email the ops-alerts topic.
# Why from MSK, not the consumer's metrics: a dead or crash-looping consumer
#       can't report that it's dead - on 14 Nov scoring stopped for 81 minutes
#       and nothing said so. Kafka keeps measuring lag whatever the consumer is
#       doing (MSK publishes it for free, even when the group is empty).
# Why here (ephemeral): the alarm is created and destroyed with the apps, so
#       the nightly teardown never trips it, and missing data counts as fine.
# Threshold: more than 5 minutes behind for 15 minutes. A cold rebuild can
#       come close (the consumer waits for the endpoint), which is real lag.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "account" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "00-account/terraform.tfstate"
    region = var.aws_region
  }
}

resource "aws_cloudwatch_metric_alarm" "consumer_lag" {
  alarm_name        = "${var.project_name}-consumer-lag"
  alarm_description = "Scoring is falling behind: the consumer group's most-behind partition would take over 5 minutes to drain, for 15 minutes. The consumer may be down, crash-looping or too slow."

  namespace   = "AWS/Kafka"
  metric_name = "EstimatedMaxTimeLag"
  dimensions = {
    "Cluster Name"   = local.ml.msk_cluster_name
    "Consumer Group" = local.ml.consumer_group
    "Topic"          = local.ml.kafka_topics["transactions"]
  }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 300
  treat_missing_data  = "notBreaching"

  alarm_actions = [data.terraform_remote_state.account.outputs.ops_alerts_topic_arn]
  ok_actions    = [data.terraform_remote_state.account.outputs.ops_alerts_topic_arn]
}
