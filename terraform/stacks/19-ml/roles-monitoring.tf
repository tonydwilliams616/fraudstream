# -----------------------------------------------------------------------------
# 19-ml/roles-monitoring.tf - roles for the hourly drift check (Phase 6c).
#
# Two roles, the same split as everywhere else in this stack:
#   - drift check TASK role: what the job's code may do. Read one hour of the
#     scored archive, read drift baselines from pipeline runs, publish metrics
#     to its own CloudWatch namespace. Nothing else - it can't write to S3,
#     touch the model or start a retrain.
#   - SCHEDULER role: what EventBridge Scheduler may do when the hour comes:
#     start that one task definition, handing it its two roles.
# Why here: roles are persistent and free, and 19-ml already owns every role and
# fixed name the ephemeral stacks use. 35-apps creates the task, schedule and
# alarm, and is destroyed every night.
# -----------------------------------------------------------------------------
locals {
  drift_check_family    = "${local.name}-drift-check"
  drift_check_namespace = "fraudstream/drift"
}

resource "aws_iam_role" "drift_check" {
  name               = "${local.name}-drift-check"
  description        = "Hourly drift check: reads the scored archive and baselines, publishes PSI metrics"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "drift_check" {
  statement {
    sid       = "ListArchive"
    actions   = ["s3:ListBucket"]
    resources = [local.buckets["scored"]]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["scored/*"]
    }
  }

  statement {
    sid       = "ReadArchive"
    actions   = ["s3:GetObject"]
    resources = ["${local.buckets["scored"]}/scored/*"]
  }

  statement {
    sid       = "ReadBaselines"
    actions   = ["s3:GetObject"]
    resources = ["${local.buckets["artefacts"]}/${local.pipeline_prefix}/runs/*/data/drift-baseline.json"]
  }

  statement {
    sid       = "PublishDriftMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData has no resource ARNs; the condition limits it to one namespace
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.drift_check_namespace]
    }
  }
}

resource "aws_iam_role_policy" "drift_check" {
  name   = "drift-check"
  role   = aws_iam_role.drift_check.id
  policy = data.aws_iam_policy_document.drift_check.json
}

# --- EventBridge Scheduler ---------------------------------------------------------
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "drift_scheduler" {
  name               = "${local.name}-drift-scheduler"
  description        = "EventBridge Scheduler: starts the hourly drift check task"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "drift_scheduler" {
  statement {
    sid       = "StartDriftCheck"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task-definition/${local.drift_check_family}:*"]
  }

  statement {
    sid       = "HandOverTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_task_execution.arn, aws_iam_role.drift_check.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "drift_scheduler" {
  name   = "drift-scheduler"
  role   = aws_iam_role.drift_scheduler.id
  policy = data.aws_iam_policy_document.drift_scheduler.json
}
