# -----------------------------------------------------------------------------
# 19-ml/variables.tf - names the roles' permissions are scoped to.
#
# Why names, not ARNs: the MSK cluster and the endpoint are rebuilt every
# session, and an MSK cluster's ARN contains a new random ID each time. These
# roles are persistent, so they're scoped by the fixed NAMES the other stacks
# will use (cluster fs-msk, endpoint fs-fraud, topics, consumer group). The
# stacks that create those resources read the same names from this stack's
# outputs, so the two can't drift apart.
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

variable "kafka_topics" {
  description = "Kafka topic names: raw transactions in, scored results and fraud alerts out."
  type = object({
    transactions = string
    scored       = string
    alerts       = string
  })
  default = {
    transactions = "transactions"
    scored       = "scored"
    alerts       = "fraud.alerts"
  }
}

variable "dataset_version" {
  description = "Dataset version whose reference tables (cards, merchants) the simulator and consumer read."
  type        = string
  default     = "transactions/v2"
}

variable "xgboost_image_account" {
  description = "AWS-owned ECR account hosting SageMaker's built-in XGBoost image in us-east-1."
  type        = string
  default     = "683313688378"
}
