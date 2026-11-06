# -----------------------------------------------------------------------------
# 30-streaming/variables.tf - broker size, Kafka version and retention.
# The cluster NAME isn't here: it comes from 19-ml, because the apps' IAM
# policies are scoped to that exact name.
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

variable "kafka_version" {
  description = "MSK Kafka version. 3.9.x is MSK's recommended long-support release; the .kraft suffix selects KRaft mode (no ZooKeeper)."
  type        = string
  default     = "3.9.x.kraft"
}

variable "broker_instance_type" {
  description = "Broker size (decision D4): Graviton m7g.large, the production standard. ~$0.204/hour each."
  type        = string
  default     = "kafka.m7g.large"
}

variable "broker_volume_gb" {
  description = "EBS storage per broker. Our traffic is tiny (a few MB per session x 3 replicas); 50 GB leaves ample room for 7 days' retention."
  type        = number
  default     = 50
}

variable "retention_hours" {
  description = "How long brokers keep messages (lets a consumer replay). The cluster is destroyed nightly anyway; S3 is the long-term record."
  type        = number
  default     = 168
}

variable "log_retention_days" {
  description = "How long broker logs are kept in CloudWatch Logs."
  type        = number
  default     = 7
}
