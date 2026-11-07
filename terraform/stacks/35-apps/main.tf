# -----------------------------------------------------------------------------
# 35-apps/main.tf - the simulator running as an ECS service on Fargate.
#
# What: a task definition (image, size, settings, logs) and a service that keeps
#       one simulator task running in the private app subnets, sending
#       synthetic transactions to the `transactions` topic on MSK.
# Why it's built this way:
#   - Image pinned by DIGEST, not tag: the task definition records the exact
#     image bytes it runs. Rebuilding or re-tagging can't change a running
#     deployment behind Terraform's back; a new image means a new task
#     definition revision and a visible plan diff.
#   - ARM64 (Graviton), matching the image build - ~20% cheaper than x86.
#   - Read-only root filesystem: the container can write only to /tmp (a
#     scratch volume where the dataset's reference tables are cached).
#     Anything that tries to modify the image at run time fails.
#   - Private subnets, no public IP. AWS API calls (ECR pulls, logs, STS) go out
#     through the NAT gateways in 15-nat; S3 through the gateway endpoint; MSK
#     is reached inside the VPC on port 9098 (the apps security group).
#   - Two roles (from 19-ml): the execution role lets ECS pull the image and
#     write logs; the task role is what the simulator itself may do (write its
#     one topic, read the reference tables) - nothing more.
#   - Deployments stop the old task before starting the new one (minimum 0%,
#     maximum 100%). The simulator replays a seeded stream, so two running at
#     once would send every transaction twice.
#   - The deployment circuit breaker rolls back automatically if new tasks keep
#     failing to start, and the apply waits until the service is stable, so a
#     broken image fails the pipeline instead of looking like a success.
# Depends on: 15-nat and 30-streaming being up (the brokers and ECS cluster).
#   If 30-streaming is torn down, the plan stops with a clear message.
# Known behaviour: SEED defaults to today's date, so a task restarted on the
#   same day replays the same transactions (same IDs). Kafka delivery is
#   at-least-once anyway, so downstream consumers must tolerate duplicates.
# Cost: 0.5 vCPU + 1 GiB ARM Fargate ~$0.02/hour, plus logs.
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

  # After a teardown the state still exists but has no outputs. Say so plainly
  # instead of failing on "Unsupported attribute" further down.
  lifecycle {
    postcondition {
      condition     = can(self.outputs.bootstrap_brokers_iam) && can(self.outputs.ecs_cluster_arn)
      error_message = "30-streaming is not up (its state has no outputs). Apply 15-nat and 30-streaming first, e.g. Terraform Deploy with stack = all."
    }
  }
}

locals {
  name      = var.project_name
  net       = data.terraform_remote_state.network.outputs
  ml        = data.terraform_remote_state.ml.outputs
  streaming = data.terraform_remote_state.streaming.outputs
  buckets   = data.terraform_remote_state.data.outputs.bucket_names
}

# --- image ----------------------------------------------------------------------
data "aws_ecr_repository" "simulator" {
  name = "${local.name}/simulator"
}

# A specific commit's image if simulator_image_tag is set, else the newest push.
data "aws_ecr_image" "simulator" {
  repository_name = data.aws_ecr_repository.simulator.name
  image_tag       = var.simulator_image_tag
  most_recent     = var.simulator_image_tag == null ? true : null
}

locals {
  simulator_image = "${data.aws_ecr_repository.simulator.repository_url}@${data.aws_ecr_image.simulator.image_digest}"
}

# --- logs -----------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "simulator" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key adds cost for no benefit on synthetic data
  #checkov:skip=CKV_AWS_338:App logs are for troubleshooting a session; 7 days is enough
  name              = "/${local.name}/apps/simulator"
  retention_in_days = var.log_retention_days
}

# --- task definition ------------------------------------------------------------
resource "aws_ecs_task_definition" "simulator" {
  #checkov:skip=CKV_AWS_249:The roles are different (fs-ecs-task-execution vs fs-simulator, both from 19-ml); checkov cannot resolve remote-state values, so it sees two unknowns as equal
  family                   = "${local.name}-simulator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.simulator_cpu
  memory                   = var.simulator_memory
  execution_role_arn       = local.ml.ecs_task_execution_role_arn
  task_role_arn            = local.ml.task_role_arns["simulator"]

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  # Scratch space on the task's own ephemeral storage, mounted at /tmp.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name                   = "simulator"
      image                  = local.simulator_image
      essential              = true
      user                   = "10001"
      readonlyRootFilesystem = true
      stopTimeout            = 30 # seconds to flush Kafka after SIGTERM

      mountPoints = [
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }
      ]

      # Settings the simulator reads at start-up (fraudstream.simulator.Settings).
      environment = [
        { name = "BOOTSTRAP_SERVERS", value = local.streaming.bootstrap_brokers_iam },
        { name = "TOPIC", value = local.ml.kafka_topics["transactions"] },
        { name = "DATASET_URI", value = "s3://${local.buckets["datasets"]}/${local.ml.dataset_version}" },
        { name = "RATE", value = tostring(var.simulator_rate) },
        { name = "AWS_REGION", value = var.aws_region },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.simulator.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "simulator"
          # If CloudWatch is slow, drop log lines rather than stall the app.
          mode            = "non-blocking"
          max-buffer-size = "25m"
        }
      }
    }
  ])
}

# --- service --------------------------------------------------------------------
resource "aws_ecs_service" "simulator" {
  name             = "${local.name}-simulator"
  cluster          = local.streaming.ecs_cluster_arn
  task_definition  = aws_ecs_task_definition.simulator.arn
  desired_count    = var.simulator_desired_count
  platform_version = "LATEST"

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }

  network_configuration {
    subnets          = local.net.app_subnet_ids
    security_groups  = [local.net.security_group_ids["apps"]]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  wait_for_steady_state   = true
  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"
}
