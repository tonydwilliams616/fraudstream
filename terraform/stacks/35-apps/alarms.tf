# -----------------------------------------------------------------------------
# 35-apps/alarms.tf - "is the consumer keeping up?" from outside the consumer.
#
# What: a CloudWatch alarm on MSK's own consumer-lag metric for our consumer
#       group: how many messages in `transactions` are waiting unread, across
#       all partitions (SumOffsetLag). Alarm and recovery both email the
#       ops-alerts topic.
# Why from MSK, not the consumer's metrics: a dead or crash-looping consumer
#       can't report that it's dead - on 14 Nov scoring stopped for 81 minutes
#       and nothing said so. Kafka keeps measuring lag whatever the consumer is
#       doing (MSK publishes it for free, even when the group is empty).
# Why here (ephemeral): the alarm is created and destroyed with the apps, so
#       the nightly teardown never trips it, and missing data counts as fine.
# Why a message count, not MSK's time estimate: EstimatedMaxTimeLag is worked
#       out from how fast the consumer is reading - with the consumer stopped
#       that speed is 0 and the estimate reads 0, so the alarm could never fire
#       for the outage it exists for. Found in the live test on 21 Nov 2026
#       (consumer scaled to 0: time lag fell to 0 while unread messages grew
#       by ~3,000 every 5 minutes). A count keeps growing whatever the consumer
#       is doing.
# Threshold: over 3,000 unread messages - 5 minutes of traffic at the
#       simulator's 10/s, ~6x the healthy level (the consumer commits once a
#       minute, so 300-600 is normal) - for 2 x 5 minutes. Email ~15 minutes
#       after scoring stops. Tied to the traffic rate: revisit if it changes.
#       A cold rebuild can come close (the consumer waits for the endpoint),
#       which is real lag.
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
  alarm_description = "Scoring is falling behind: over 3,000 transactions (about 5 minutes of traffic) are waiting unread in Kafka, for 10 minutes. The consumer may be down, crash-looping or too slow."

  namespace   = "AWS/Kafka"
  metric_name = "SumOffsetLag"
  dimensions = {
    "Cluster Name"   = local.ml.msk_cluster_name
    "Consumer Group" = local.ml.consumer_group
    "Topic"          = local.ml.kafka_topics["transactions"]
  }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 3000
  treat_missing_data  = "notBreaching"

  alarm_actions = [data.terraform_remote_state.account.outputs.ops_alerts_topic_arn]
  ok_actions    = [data.terraform_remote_state.account.outputs.ops_alerts_topic_arn]
}
