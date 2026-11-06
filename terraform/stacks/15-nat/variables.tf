# -----------------------------------------------------------------------------
# 15-nat/variables.tf - naming, region and account. Everything else (which
# subnets, which route tables) is read from the 10-network stack's outputs, so
# the two stacks can never disagree about the network layout.
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
