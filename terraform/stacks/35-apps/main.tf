# -----------------------------------------------------------------------------
# 35-apps/main.tf - the two streaming apps as ECS services on Fargate.
#
#   simulator - sends synthetic transactions to the `transactions` topic
#   consumer  - scores them with the deployed model: `scored` and
#               `fraud.alerts` topics, plus a one-minute archive in S3
#
# Both use modules/ecs-service (image pinned by digest, read-only root, private
# subnets, circuit breaker with rollback, apply waits for a stable service).
# What differs is how each one is replaced during a deployment:
#   - simulator: stop the old task BEFORE starting the new one (0% / 100%).
#     It replays a seeded stream, so two at once would send every transaction
#     twice. desired_count is 0 or 1.
#   - consumer: start the new task BEFORE stopping the old one (100% / 200%).
#     Both are in one consumer group, so Kafka hands partitions over between
#     them (the old one checkpoints first) - no gap in scoring. It can run up
#     to 6 tasks, one per partition; a 7th would sit idle.
# Start-up order: 35-apps is applied before 40-serving, so on a rebuild the
#   consumer can start before the endpoint exists. It waits (logging why) rather
#   than crashing, so the service is stable and the apply carries on.
# Depends on: 15-nat and 30-streaming being up. If 30-streaming is torn down,
#   the plan stops with a clear message.
# Known behaviour: the simulator's SEED defaults to today's date, so a task
#   restarted on the same day replays the same transactions (same IDs). The
#   consumer is at-least-once anyway; records carry transaction_id for de-dup.
# Cost: 2 x (0.5 vCPU + 1 GiB ARM Fargate) ~$0.04/hour, plus logs.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "data" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "18-data/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "10-network/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "ml" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "19-ml/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "streaming" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "30-streaming/terraform.tfstate"
    region = var.aws_region
  }

  # After a teardown the state still exists but has no outputs; while a deploy is
  # still creating MSK it has some outputs but no broker addresses yet. Say so
  # plainly instead of failing on "Unsupported attribute" further down.
  lifecycle {
    postcondition {
      condition     = can(self.outputs.bootstrap_brokers_iam) && can(self.outputs.ecs_cluster_arn)
      error_message = "30-streaming isn't fully up (its state has no MSK broker addresses or ECS cluster). Apply 15-nat and 30-streaming first (Terraform Deploy, stack = all), or wait for a running deploy to finish creating MSK."
    }
  }
}

locals {
  net       = data.terraform_remote_state.network.outputs
  ml        = data.terraform_remote_state.ml.outputs
  streaming = data.terraform_remote_state.streaming.outputs
  buckets   = data.terraform_remote_state.data.outputs.bucket_names
}

locals {
  common = {
    project_name       = var.project_name
    aws_region         = var.aws_region
    cluster_arn        = local.streaming.ecs_cluster_arn
    execution_role_arn = local.ml.ecs_task_execution_role_arn
    subnet_ids         = local.net.app_subnet_ids
    security_group_ids = [local.net.security_group_ids["apps"]]
    log_retention_days = var.log_retention_days
  }
  dataset_uri = "s3://${local.buckets["datasets"]}/${local.ml.dataset_version}"
}

module "simulator" {
  source = "../../modules/ecs-service"

  app                = "simulator"
  project_name       = local.common.project_name
  aws_region         = local.common.aws_region
  cluster_arn        = local.common.cluster_arn
  execution_role_arn = local.common.execution_role_arn
  subnet_ids         = local.common.subnet_ids
  security_group_ids = local.common.security_group_ids
  log_retention_days = local.common.log_retention_days

  task_role_arn = local.ml.task_role_arns["simulator"]
  image_tag     = var.simulator_image_tag
  cpu           = var.simulator_cpu
  memory        = var.simulator_memory
  desired_count = var.simulator_desired_count
  stop_timeout  = 30 # flush Kafka

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  # Settings the simulator reads at start-up (fraudstream.simulator.Settings).
  environment = {
    BOOTSTRAP_SERVERS = local.streaming.bootstrap_brokers_iam
    TOPIC             = local.ml.kafka_topics["transactions"]
    DATASET_URI       = local.dataset_uri
    RATE              = tostring(var.simulator_rate)
    AWS_REGION        = var.aws_region
  }
}

module "consumer" {
  source = "../../modules/ecs-service"

  app                = "consumer"
  project_name       = local.common.project_name
  aws_region         = local.common.aws_region
  cluster_arn        = local.common.cluster_arn
  execution_role_arn = local.common.execution_role_arn
  subnet_ids         = local.common.subnet_ids
  security_group_ids = local.common.security_group_ids
  log_retention_days = local.common.log_retention_days

  task_role_arn = local.ml.task_role_arns["consumer"]
  image_tag     = var.consumer_image_tag
  cpu           = var.consumer_cpu
  memory        = var.consumer_memory
  desired_count = var.consumer_desired_count
  stop_timeout  = 60 # flush Kafka, write the archive file, commit

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # Settings the consumer reads at start-up (fraudstream.consumer.Settings).
  environment = {
    BOOTSTRAP_SERVERS = local.streaming.bootstrap_brokers_iam
    INPUT_TOPIC       = local.ml.kafka_topics["transactions"]
    SCORED_TOPIC      = local.ml.kafka_topics["scored"]
    ALERTS_TOPIC      = local.ml.kafka_topics["alerts"]
    GROUP_ID          = local.ml.consumer_group
    ENDPOINT_NAME     = local.ml.endpoint_name
    DATASET_URI       = local.dataset_uri
    ARCHIVE_BUCKET    = local.buckets["scored"]
    AWS_REGION        = var.aws_region
  }
}

# The simulator's resources used to be defined directly in this stack. These
# tell Terraform they now live in the module, so it moves them in state instead
# of destroying and recreating the running service.
moved {
  from = aws_cloudwatch_log_group.simulator
  to   = module.simulator.aws_cloudwatch_log_group.this
}

moved {
  from = aws_ecs_task_definition.simulator
  to   = module.simulator.aws_ecs_task_definition.this
}

moved {
  from = aws_ecs_service.simulator
  to   = module.simulator.aws_ecs_service.this
}
