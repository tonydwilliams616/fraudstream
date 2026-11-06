# -----------------------------------------------------------------------------
# 10-network/variables.tf - address space, availability zones and log retention.
#
# Why AZ IDs, not names: "us-east-1a" points at a different physical zone in
# every AWS account, but an AZ ID (use1-az1) is the same zone for everyone.
# AWS documents service limits by AZ ID - for example, MSK brokers can't be
# placed in use1-az3 - so pinning IDs is what makes the choice reliable.
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

variable "vpc_cidr" {
  description = "VPC address range. 10.40.0.0/16 here; the DR region (Phase 7) gets a different, non-overlapping range so the two could be peered."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_ids" {
  description = "Exactly three AZ IDs, one broker/NAT/subnet set each. use1-az3 is excluded because MSK can't place brokers there."
  type        = list(string)
  default     = ["use1-az1", "use1-az2", "use1-az4"]

  validation {
    condition     = length(var.az_ids) == 3 && length(distinct(var.az_ids)) == 3
    error_message = "Provide exactly three different AZ IDs (one per MSK broker)."
  }
}

variable "flow_log_retention_days" {
  description = "How long VPC flow logs are kept in CloudWatch Logs."
  type        = number
  default     = 14
}
