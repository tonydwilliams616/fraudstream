# -----------------------------------------------------------------------------
# 05-registry/variables.tf - which image repositories to create.
#
# Why the fs/ prefix: the CI push role (bootstrap) may only push to fs/*, so a
# new repository is usable from CI just by adding it to this list.
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

variable "repositories" {
  description = "Image repositories to create, under the <project_name>/ prefix the CI push role is scoped to."
  type        = set(string)
  default = [
    "simulator", # produces synthetic card transactions to Kafka (Phase 2)
    "consumer",  # reads Kafka, builds features, calls the SageMaker endpoint (Phase 2)
  ]
}

variable "images_to_keep" {
  description = "Tagged images kept per repository; older ones expire."
  type        = number
  default     = 5
}
