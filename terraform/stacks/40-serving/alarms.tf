# -----------------------------------------------------------------------------
# 40-serving/alarms.tf - the alarms that roll a bad model back.
#
# What: three CloudWatch alarms on the fs-fraud endpoint's AllTraffic variant:
#         errors-5xx   any 5xx response from the model in a minute
#         errors-4xx   any 4xx response in a minute (a model that crashes on our
#                      input is reported to the caller as a 4xx "ModelError")
#         latency-p99  99th-percentile model latency above 500 ms for 2 minutes
#       They're wired into the endpoint's blue/green deployment (main.tf): if any
#       fires while a new model is baking, SageMaker sends all traffic back to the
#       old model on its own - no human, no Terraform run.
# Why these: they catch a model that is BROKEN or SLOW within minutes. They can't
#       catch a model that is quietly WRONG (worse fraud detection) - that needs
#       labels, which arrive days later; that's the evaluation gate's job before
#       deployment, and drift monitoring's job after it.
# Thresholds: errors - any at all, because the consumer sends ~12 well-formed
#       requests a minute and a healthy model never fails one. Latency - normal
#       model latency for a 50-row batch is milliseconds; 500 ms is far above
#       normal but quiet enough not to roll back on a single slow request.
# Missing data counts as OK: no traffic is not a reason to roll back.
# Ephemeral: destroyed and rebuilt with the endpoint (free while up, ~$0.10 per
#       alarm per month pro rata).
# -----------------------------------------------------------------------------
locals {
  # The endpoint NAME (not a reference to the endpoint resource): the endpoint
  # refers to these alarms, so referring back would be a dependency cycle.
  alarm_dimensions = {
    EndpointName = local.ml.endpoint_name
    VariantName  = "AllTraffic"
  }
}

resource "aws_cloudwatch_metric_alarm" "errors_5xx" {
  alarm_name          = "${local.name}-fraud-errors-5xx"
  alarm_description   = "fs-fraud returned 5xx errors - rolls back a blue/green deployment"
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation5XXErrors"
  dimensions          = local.alarm_dimensions
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "errors_4xx" {
  alarm_name          = "${local.name}-fraud-errors-4xx"
  alarm_description   = "fs-fraud returned 4xx errors (incl. ModelError) - rolls back a blue/green deployment"
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation4XXErrors"
  dimensions          = local.alarm_dimensions
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "latency_p99" {
  alarm_name          = "${local.name}-fraud-latency-p99"
  alarm_description   = "fs-fraud p99 model latency above 500 ms for 2 minutes - rolls back a blue/green deployment"
  namespace           = "AWS/SageMaker"
  metric_name         = "ModelLatency" # reported in MICROseconds
  dimensions          = local.alarm_dimensions
  extended_statistic  = "p99"
  period              = 60
  evaluation_periods  = 2
  threshold           = 500000 # 500 ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
