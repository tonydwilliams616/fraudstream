# -----------------------------------------------------------------------------
# 35-apps/drift-check.tf - the hourly drift check (Phase 6c): task, schedule, alarm.
#
# What: at 5 past every hour, EventBridge Scheduler starts one Fargate task in
#       the fs-apps cluster running `python -m fraudstream.monitoring check`
#       (fraudstream/monitoring/check.py). It compares the previous hour of the
#       scored archive with the serving model's own drift baseline, publishes
#       PSI per feature to CloudWatch (namespace fraudstream/drift), and exits.
#       An alarm on MaxAlarmPSI > 0.25 emails the ops-alerts topic; Phase 6f
#       will also start a retrain from it.
# Why:  SageMaker Model Monitor is closed to new AWS customers, so drift is our
#       own code. A scheduled task (the ECS version of a Kubernetes CronJob)
#       runs for about a minute an hour and costs nothing in between.
# Image: the PIPELINE image (fs/pipeline), pinned by digest - it already has the
#       whole package plus pandas, and is built, scanned and pushed on every
#       merge. Only the entry point differs. It's built for x86 (SageMaker
#       Processing runs on x86), so this task is x86 too.
# Alarm: datapoints are published once an hour, so the alarm looks at the last
#       two hourly periods and fires if either is over 0.25. Missing data is
#       "not breaching": a quiet hour is not drift (the lag alarm watches for
#       a dead consumer). It clears after two hours of normal data.
# Roles: both come from 19-ml (roles-monitoring.tf) - read-only S3 + one
#       metrics namespace for the task, RunTask on this one family for the
#       scheduler.
# Cost: 0.5 vCPU / 1 GB x86 for ~1 minute an hour + ~16 custom metrics
#       (prorated) - about $0.01 for each hour the stack is up. Ephemeral:
#       destroyed with the rest of 35-apps every night.
# -----------------------------------------------------------------------------
data "aws_ecr_repository" "pipeline" {
  name = "${var.project_name}/pipeline"
}

data "aws_ecr_image" "pipeline" {
  repository_name = data.aws_ecr_repository.pipeline.name
  image_tag       = var.pipeline_image_tag
  most_recent     = var.pipeline_image_tag == null ? true : null
}

resource "aws_cloudwatch_log_group" "drift_check" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key adds cost for no benefit on synthetic data
  #checkov:skip=CKV_AWS_338:Job logs are for troubleshooting a session; 7 days is enough
  name              = "/${var.project_name}/apps/drift-check"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "drift_check" {
  #checkov:skip=CKV_AWS_249:Two different roles from 19-ml (task execution vs the job's own role); checkov can't resolve remote state, so it sees two unknowns as equal
  family                   = local.ml.drift_check.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = local.ml.ecs_task_execution_role_arn
  task_role_arn            = local.ml.drift_check.task_role_arn

  runtime_platform {
    cpu_architecture        = "X86_64" # the pipeline image is built for x86
    operating_system_family = "LINUX"
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "drift-check"
      image                  = "${data.aws_ecr_repository.pipeline.repository_url}@${data.aws_ecr_image.pipeline.image_digest}"
      essential              = true
      user                   = "10001"
      readonlyRootFilesystem = true
      entryPoint             = ["python", "-m", "fraudstream.monitoring"]
      command                = ["check"]

      mountPoints = [
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }
      ]

      environment = [
        { name = "SCORED_BUCKET", value = local.buckets["scored"] },
        { name = "ARTEFACTS_BUCKET", value = local.buckets["artefacts"] },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.drift_check.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "drift-check"
        }
      }
    }
  ])
}

resource "aws_scheduler_schedule" "drift_check" {
  #checkov:skip=CKV_AWS_297:The schedule holds only a task ARN and network settings - nothing sensitive to encrypt with a customer-managed key
  name                         = "${var.project_name}-drift-check"
  description                  = "Hourly drift check: previous hour of the scored archive vs the model's training baseline"
  schedule_expression          = var.drift_check_schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = local.streaming.ecs_cluster_arn
    role_arn = local.ml.drift_check.scheduler_role_arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.drift_check.arn
      launch_type         = "FARGATE"
      task_count          = 1

      network_configuration {
        subnets          = local.net.app_subnet_ids
        security_groups  = [local.net.security_group_ids["apps"]]
        assign_public_ip = false
      }
    }

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "data_drift" {
  alarm_name        = "${var.project_name}-data-drift"
  alarm_description = "Live transactions no longer look like the serving model's training data: a purchase feature's PSI is over 0.25 (significant). Check the drift metrics by feature, rule out a code change (skew), then consider a retrain."

  namespace   = local.ml.drift_check.namespace
  metric_name = "MaxAlarmPSI"

  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 2
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0.25
  treat_missing_data  = "notBreaching"

  alarm_actions = [data.terraform_remote_state.account.outputs.ops_alerts_topic_arn]
  ok_actions    = [data.terraform_remote_state.account.outputs.ops_alerts_topic_arn]
}
