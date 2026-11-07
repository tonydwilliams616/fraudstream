# -----------------------------------------------------------------------------
# 35-apps/variables.tf - which image to run, how fast to send, and task size.
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
