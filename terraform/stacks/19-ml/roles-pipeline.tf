# -----------------------------------------------------------------------------
# 19-ml/roles-pipeline.tf - the role the SageMaker training pipeline runs as.
#
# The pipeline (fs-fraud-training, defined in Python and upserted by GitHub
# Actions) uses this one role both to start its jobs and inside them:
#   prepare  (fs/pipeline image)       read a dataset version
#   train    (SageMaker XGBoost image) read the prepared data, write the model
#   evaluate (fs/pipeline image)       read the champion from the registry
#   register (fs/pipeline image)       add the challenger to fs-fraud-detection
# What it may do, and nothing more:
#   - read datasets/transactions/*; read artefacts/models/* (champions, code)
#   - write only under artefacts/models/fraud-pipeline/* (its own runs)
#   - pull its two images; write its logs and training metrics
#   - create/describe/stop processing and training jobs, passing ONLY itself
#   - list/describe packages in fs-fraud-detection and create new ones there -
#     registering as PendingManualApproval is the code's job; approving stays
#     a person's decision (this role can't approve: no UpdateModelPackage)
# Everything is under models/ so the endpoint's role (models/* read-only) can
# serve a pipeline-trained model once it's approved and promoted.
# -----------------------------------------------------------------------------
locals {
  pipeline_name   = "${local.name}-fraud-training"
  pipeline_prefix = "models/fraud-pipeline"
  sagemaker_arn   = "arn:aws:sagemaker:${var.aws_region}:${var.aws_account_id}"
}

resource "aws_iam_role" "sagemaker_pipeline" {
  name               = "${local.name}-sagemaker-pipeline"
  description        = "Runs the fs-fraud-training SageMaker Pipeline and its jobs"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
}

data "aws_iam_policy_document" "sagemaker_pipeline" {
  #checkov:skip=CKV_AWS_356:PutMetricData and ListModelPackages accept no resource ARN; PutMetricData is limited to SageMaker namespaces by a condition
  statement {
    sid       = "ReadDatasets"
    actions   = ["s3:GetObject"]
    resources = ["${local.buckets["datasets"]}/transactions/*"]
  }

  statement {
    sid       = "ListDatasets"
    actions   = ["s3:ListBucket"]
    resources = [local.buckets["datasets"]]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["transactions/*"]
    }
  }

  statement {
    sid       = "ReadModels"
    actions   = ["s3:GetObject"]
    resources = ["${local.buckets["artefacts"]}/models/*"]
  }

  statement {
    sid       = "WritePipelineRuns"
    actions   = ["s3:PutObject"]
    resources = ["${local.buckets["artefacts"]}/${local.pipeline_prefix}/*"]
  }

  statement {
    sid       = "ListModels"
    actions   = ["s3:ListBucket"]
    resources = [local.buckets["artefacts"]]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*"]
    }
  }

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # account-level call, can't be scoped to a repository
  }

  statement {
    sid = "PullStepImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [
      "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${local.name}/pipeline",
      "arn:aws:ecr:${var.aws_region}:${var.xgboost_image_account}:repository/sagemaker-xgboost",
    ]
  }

  statement {
    sid = "JobLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/sagemaker/*"]
  }

  statement {
    sid       = "TrainingMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData has no resource ARN; limited to SageMaker namespaces

    condition {
      test     = "StringLike"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/*"]
    }
  }

  # Pipelines name their jobs themselves (pipelines-<id>-<step>-<hash>), so the
  # jobs can't be scoped by a fs- prefix; they are scoped to this account/region.
  statement {
    sid = "RunPipelineJobs"
    actions = [
      "sagemaker:AddTags",
      "sagemaker:CreateProcessingJob",
      "sagemaker:CreateTrainingJob",
      "sagemaker:DescribeProcessingJob",
      "sagemaker:DescribeTrainingJob",
      "sagemaker:StopProcessingJob",
      "sagemaker:StopTrainingJob",
    ]
    resources = [
      "${local.sagemaker_arn}:processing-job/*",
      "${local.sagemaker_arn}:training-job/*",
    ]
  }

  statement {
    sid       = "PassOnlyItself"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.sagemaker_pipeline.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }

  statement {
    sid       = "ListModelPackages"
    actions   = ["sagemaker:ListModelPackages"]
    resources = ["*"] # a list call with no resource ARN
  }

  statement {
    sid = "ReadAndRegisterPackages"
    actions = [
      "sagemaker:CreateModelPackage",
      "sagemaker:DescribeModelPackage",
      "sagemaker:DescribeModelPackageGroup",
    ]
    resources = [
      aws_sagemaker_model_package_group.fraud.arn,
      "${local.sagemaker_arn}:model-package/${aws_sagemaker_model_package_group.fraud.model_package_group_name}/*",
    ]
  }
}

resource "aws_iam_role_policy" "sagemaker_pipeline" {
  name   = "pipeline"
  role   = aws_iam_role.sagemaker_pipeline.id
  policy = data.aws_iam_policy_document.sagemaker_pipeline.json
}
