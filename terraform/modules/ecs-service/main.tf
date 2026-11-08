# -----------------------------------------------------------------------------
# modules/ecs-service/main.tf - one app as an ECS service on Fargate, hardened
# the same way every time.
#
# What: the app's image (pinned by digest), its log group, task definition and
#       service. 35-apps uses it for the simulator and the consumer.
# Why a module: both apps need the same safeguards, and a third (Grafana in
#       Phase 5) will too. Written once, a new app can't be deployed without them:
#   - Image pinned by DIGEST, not tag: the task definition records the exact
#     image bytes it runs. A new image means a new task definition revision and
#     a visible plan diff - never a silent change behind Terraform's back.
#   - ARM64 (Graviton), matching the image build - ~20% cheaper than x86.
#   - Read-only root filesystem; /tmp is a scratch volume (the images declare
#     VOLUME /tmp so it's writable by the unprivileged user).
#   - Private subnets, no public IP. AWS APIs are reached through the NAT
#     gateways (15-nat), S3 through the gateway endpoint, MSK inside the VPC.
#   - Two roles: the execution role lets ECS pull the image and write logs; the
#     task role is what the app itself may do (19-ml) - nothing more.
#   - The deployment circuit breaker rolls back if new tasks keep failing, and
#     the apply waits until the service is stable, so a broken image fails the
#     pipeline instead of looking like a success.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# --- image ----------------------------------------------------------------------
data "aws_ecr_repository" "this" {
  name = "${var.project_name}/${var.app}"
}

# A specific commit's image if image_tag is set, else the newest push.
data "aws_ecr_image" "this" {
  repository_name = data.aws_ecr_repository.this.name
  image_tag       = var.image_tag
  most_recent     = var.image_tag == null ? true : null
}

locals {
  image = "${data.aws_ecr_repository.this.repository_url}@${data.aws_ecr_image.this.image_digest}"
}

# --- logs -----------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "this" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key adds cost for no benefit on synthetic data
  #checkov:skip=CKV_AWS_338:App logs are for troubleshooting a session; 7 days is enough
  name              = "/${var.project_name}/apps/${var.app}"
  retention_in_days = var.log_retention_days
}

# --- task definition ------------------------------------------------------------
resource "aws_ecs_task_definition" "this" {
  #checkov:skip=CKV_AWS_249:The caller passes two different roles (task execution vs the app's own role, both from 19-ml); checkov can't resolve them, so it sees two unknowns as equal
  family                   = "${var.project_name}-${var.app}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

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
      name                   = var.app
      image                  = local.image
      essential              = true
      user                   = "10001"
      readonlyRootFilesystem = true
      stopTimeout            = var.stop_timeout # seconds to finish up after SIGTERM

      mountPoints = [
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }
      ]

      environment = [for k, v in var.environment : { name = k, value = v }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = var.app
          # If CloudWatch is slow, drop log lines rather than stall the app.
          mode            = "non-blocking"
          max-buffer-size = "25m"
        }
      }
    }
  ])
}

# --- service --------------------------------------------------------------------
resource "aws_ecs_service" "this" {
  name             = "${var.project_name}-${var.app}"
  cluster          = var.cluster_arn
  task_definition  = aws_ecs_task_definition.this.arn
  desired_count    = var.desired_count
  platform_version = "LATEST"

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = var.deployment_minimum_healthy_percent
  deployment_maximum_percent         = var.deployment_maximum_percent

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  wait_for_steady_state   = true
  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"
}
