# -----------------------------------------------------------------------------
# 19-ml/roles-ecs.tf - roles for the Fargate tasks.
#
# Two kinds of role, which is how ECS separates concerns:
#   - TASK EXECUTION role: used by ECS itself, before the code starts, to pull
#     the image from ECR and send logs to CloudWatch. Shared by every task.
#   - TASK role: what the code inside the container may do. One per app, each
#     with only what that app needs.
#
# Kafka permissions use MSK IAM access control: every Kafka action (connect,
# write to a topic, read it, join a consumer group) is an IAM action checked
# per request, so each app is limited to its own topics:
#   simulator  - create + write `transactions`
#   consumer   - read `transactions` as group fs-scorer; create + write `scored`
#                and `fraud.alerts`; invoke the endpoint; archive to S3
# Topics are created by the app that writes them (create-if-missing at start),
# because auto topic creation is switched off on the cluster.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

# --- task execution (shared) ----------------------------------------------------
resource "aws_iam_role" "ecs_task_execution" {
  name               = "${local.name}-ecs-task-execution"
  description        = "Used by ECS to pull images and write logs for fraudstream tasks"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --- shared statements ----------------------------------------------------------
data "aws_iam_policy_document" "reference_tables" {
  # Both apps load the cards and merchants tables of the dataset version, so
  # live data and features match what the model was trained on.
  statement {
    sid       = "ReadReferenceTables"
    actions   = ["s3:GetObject"]
    resources = ["${local.buckets["datasets"]}/${var.dataset_version}/*"]
  }
}

# --- simulator --------------------------------------------------------------------
resource "aws_iam_role" "simulator" {
  name               = "${local.name}-simulator"
  description        = "Simulator task: produces synthetic transactions to Kafka"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "simulator" {
  source_policy_documents = [data.aws_iam_policy_document.reference_tables.json]

  statement {
    sid       = "KafkaConnect"
    actions   = ["kafka-cluster:Connect", "kafka-cluster:WriteDataIdempotently"]
    resources = [local.kafka_cluster]
  }

  statement {
    sid = "KafkaProduceTransactions"
    actions = [
      "kafka-cluster:CreateTopic",
      "kafka-cluster:DescribeTopic",
      "kafka-cluster:WriteData",
    ]
    resources = [local.kafka_topic["transactions"]]
  }
}

resource "aws_iam_role_policy" "simulator" {
  name   = "simulator"
  role   = aws_iam_role.simulator.id
  policy = data.aws_iam_policy_document.simulator.json
}

# --- consumer ---------------------------------------------------------------------
resource "aws_iam_role" "consumer" {
  name               = "${local.name}-consumer"
  description        = "Consumer task: scores transactions and publishes results and alerts"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "consumer" {
  source_policy_documents = [data.aws_iam_policy_document.reference_tables.json]

  statement {
    sid       = "KafkaConnect"
    actions   = ["kafka-cluster:Connect", "kafka-cluster:WriteDataIdempotently"]
    resources = [local.kafka_cluster]
  }

  statement {
    sid       = "KafkaConsumeTransactions"
    actions   = ["kafka-cluster:DescribeTopic", "kafka-cluster:ReadData"]
    resources = [local.kafka_topic["transactions"]]
  }

  statement {
    sid       = "KafkaConsumerGroup"
    actions   = ["kafka-cluster:DescribeGroup", "kafka-cluster:AlterGroup"]
    resources = [local.kafka_group]
  }

  statement {
    sid = "KafkaProduceResults"
    actions = [
      "kafka-cluster:CreateTopic",
      "kafka-cluster:DescribeTopic",
      "kafka-cluster:WriteData",
    ]
    resources = [local.kafka_topic["scored"], local.kafka_topic["alerts"]]
  }

  statement {
    sid       = "InvokeFraudEndpoint"
    actions   = ["sagemaker:InvokeEndpoint"]
    resources = ["arn:aws:sagemaker:${var.aws_region}:${var.aws_account_id}:endpoint/${local.endpoint_name}"]
  }

  statement {
    sid       = "ArchiveScoredEvents"
    actions   = ["s3:PutObject"]
    resources = ["${local.buckets["scored"]}/*"]
  }
}

resource "aws_iam_role_policy" "consumer" {
  name   = "consumer"
  role   = aws_iam_role.consumer.id
  policy = data.aws_iam_policy_document.consumer.json
}
