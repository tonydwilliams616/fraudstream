# -----------------------------------------------------------------------------
# 35-apps/variables.tf - which images to run, how fast to send, and task sizes.
# Names (topics, roles, buckets, cluster) aren't here: they come from the
# stacks that own them, so the apps always match their IAM permissions.
# -----------------------------------------------------------------------------
variable "project_name" {
  description = "Short prefix used for naming resources (fs = fraudstream)."
  type        = string
  default     = "fs"
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "The fraudstream-dev account ID. The provider refuses to run against any other account."
  type        = string
  default     = "831547420318"
}

variable "simulator_image_tag" {
  description = "Simulator image tag (a commit SHA) to run. null = the most recently pushed image. Either way the task definition pins the image by digest."
  type        = string
  default     = null
}

variable "simulator_rate" {
  description = "Transactions per second the simulator sends (~864,000 a day at 10)."
  type        = number
  default     = 10
}

variable "simulator_desired_count" {
  description = "Simulator tasks to run. Keep at 1: each task replays the same seeded stream, so a second task would send duplicates, not more traffic."
  type        = number
  default     = 1

  validation {
    condition     = var.simulator_desired_count >= 0 && var.simulator_desired_count <= 1
    error_message = "simulator_desired_count must be 0 (paused) or 1."
  }
}

variable "simulator_cpu" {
  description = "Task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "simulator_memory" {
  description = "Task memory in MiB. The reference tables plus one week's generated block fit comfortably in 1 GiB."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "How long app logs are kept in CloudWatch Logs."
  type        = number
  default     = 7
}

variable "consumer_image_tag" {
  description = "Consumer image tag (a commit SHA) to run. null = the most recently pushed image. Either way the task definition pins the image by digest."
  type        = string
  default     = null
}

variable "consumer_desired_count" {
  description = "Consumer tasks. Up to 6 - one per partition of `transactions`; more would sit idle. 0 pauses scoring (transactions wait in Kafka)."
  type        = number
  default     = 1

  validation {
    condition     = var.consumer_desired_count >= 0 && var.consumer_desired_count <= 6
    error_message = "consumer_desired_count must be between 0 and 6 (the topic has 6 partitions)."
  }
}

variable "consumer_cpu" {
  description = "Consumer task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "consumer_memory" {
  description = "Consumer task memory in MiB: reference tables, every card's recent history, up to a minute of buffered records, plus ~100 MiB for the metrics collector beside it."
  type        = number
  default     = 2048

  # Fargate only accepts set CPU/memory pairs, and checks them when the task
  # definition is created - after the plan. Catch a bad pair at plan time.
  validation {
    condition = contains(
      lookup({ 256 = [512, 1024, 2048], 512 = [1024, 2048, 3072, 4096], 1024 = [2048, 3072, 4096, 5120, 6144, 7168, 8192] }, var.consumer_cpu, []),
      var.consumer_memory
    )
    error_message = "Not a Fargate CPU/memory pair: at 512 CPU, memory must be 1024, 2048, 3072 or 4096 MiB (see the ECS task size table)."
  }
}

variable "consumer_metrics_port" {
  description = "Port of the consumer's Prometheus /metrics page (inside the task only)."
  type        = number
  default     = 8000
}

variable "collector_image" {
  description = "ADOT collector image for the consumer's metrics sidecar, pinned to a version tag."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.50.0"
}

variable "pipeline_image_tag" {
  description = "Pipeline image tag (a commit SHA) for the hourly drift check. null = the most recently pushed image. Pinned by digest either way."
  type        = string
  default     = null
}

variable "drift_check_schedule" {
  description = "When the drift check runs (EventBridge Scheduler expression, UTC). 5 past the hour, so the previous hour's archive is complete."
  type        = string
  default     = "cron(5 * * * ? *)"
}
