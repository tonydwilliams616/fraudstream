# -----------------------------------------------------------------------------
# 19-ml/roles-sagemaker.tf - the role the SageMaker endpoint runs as.
#
# What it may do, and nothing more:
#   - read model packages from the artefacts bucket (models/*)
#   - write endpoint data capture (requests + scores) to the capture bucket,
#     which Model Monitor reads in Phase 4 to detect drift
#   - pull the built-in XGBoost image from AWS's own ECR repository
#   - write its container logs to CloudWatch (/aws/sagemaker/*)
#   - read the endpoint's rollback alarms (fs-fraud-*): during a blue/green
#     deployment SageMaker checks them AS THIS ROLE; without this permission
#     the new fleet is abandoned before the canary even starts
# Phase 3 (training pipeline) adds its own permissions when it's built.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "sagemaker_execution" {
  name               = "${local.name}-sagemaker-execution"
  description        = "Runs the fraud-scoring SageMaker endpoint"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
}

data "aws_iam_policy_document" "sagemaker_execution" {
  statement {
    sid       = "ReadModelArtefacts"
    actions   = ["s3:GetObject"]
    resources = ["${local.buckets["artefacts"]}/models/*"]
  }

  statement {
    sid       = "WriteDataCapture"
    actions   = ["s3:PutObject"]
    resources = ["${local.buckets["capture"]}/*"]
  }

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # account-level call, can't be scoped to a repository
  }

  statement {
    sid = "PullBuiltInXgboostImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${var.xgboost_image_account}:repository/sagemaker-xgboost"]
  }

  statement {
    sid = "ContainerLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/sagemaker/*"]
  }

  statement {
    sid       = "ReadRollbackAlarms"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["arn:aws:cloudwatch:${var.aws_region}:${var.aws_account_id}:alarm:${local.name}-fraud-*"]
  }
}

resource "aws_iam_role_policy" "sagemaker_execution" {
  name   = "endpoint"
  role   = aws_iam_role.sagemaker_execution.id
  policy = data.aws_iam_policy_document.sagemaker_execution.json
}
