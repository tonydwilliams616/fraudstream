# -----------------------------------------------------------------------------
# modules/ecs-service/variables.tf - what differs between apps.
# -----------------------------------------------------------------------------
variable "project_name" {
  description = "Prefix for names (fs). The image comes from <project_name>/<app> in ECR."
  type        = string
}

variable "app" {
  description = "App name: simulator, consumer, ... Names the service, task family, container and log group."
  type        = string
}

variable "aws_region" {
  description = "AWS region (for the log driver)."
  type        = string
}

variable "image_tag" {
  description = "Image tag (a commit SHA) to run. null = the most recently pushed image. Either way the task definition pins it by digest."
  type        = string
  default     = null
}

variable "cluster_arn" {
  description = "ECS cluster to run in."
  type        = string
}

variable "cpu" {
  description = "Task CPU units (1024 = 1 vCPU)."
  type        = number
}

variable "memory" {
  description = "Task memory in MiB."
  type        = number
}

variable "execution_role_arn" {
  description = "Role ECS uses to pull the image and write logs."
  type        = string
}

variable "task_role_arn" {
  description = "Role the app itself runs as."
  type        = string
}

variable "environment" {
  description = "Settings passed to the app as environment variables."
  type        = map(string)
  default     = {}
}

variable "subnet_ids" {
  description = "Private subnets to run tasks in."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security groups for the tasks."
  type        = list(string)
}

variable "desired_count" {
  description = "Tasks to run."
  type        = number
  default     = 1
}

variable "deployment_minimum_healthy_percent" {
  description = "During a deployment, the share of desired tasks that must keep running. 0 = stop old before starting new."
  type        = number
  default     = 100
}

variable "deployment_maximum_percent" {
  description = "During a deployment, the most tasks allowed at once, as a share of desired. 100 = never overlap."
  type        = number
  default     = 200
}

variable "stop_timeout" {
  description = "Seconds between SIGTERM and SIGKILL, for the app to finish up cleanly."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "How long app logs are kept."
  type        = number
  default     = 7
}
