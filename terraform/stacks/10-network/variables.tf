# -----------------------------------------------------------------------------
# 10-network/variables.tf - address space, availability zones and log retention.
#
# Why AZ IDs, not names: "us-east-1a" points at a different physical zone in
# every AWS account, but an AZ ID (use1-az1) is the same zone for everyone.
# AWS documents service limits by AZ ID - for example, MSK brokers can't be
# placed in use1-az3 - so pinning IDs is what makes the choice reliable.
# Why a fixed slot per AZ: each AZ's subnet ranges are worked out from its slot
# number. If they came from its position in a list instead, reordering the list
# would hand every AZ different ranges and Terraform would replace the subnets
# (and everything in them). A slot never moves; a new AZ takes an unused slot.
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
  description = "VPC address range - must be a /16. 10.40.0.0/16 here; the DR region (Phase 7) gets a different, non-overlapping range so the two could be peered."
  type        = string
  default     = "10.40.0.0/16"

  # The subnet layout in main.tf (/24s and /20s) is sized for a /16.
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && endswith(var.vpc_cidr, "/16")
    error_message = "vpc_cidr must be a valid IPv4 /16, e.g. 10.40.0.0/16."
  }
}

variable "az_slots" {
  description = "AZ ID -> fixed slot number (0-5) that decides its subnet ranges. Exactly three AZs, one MSK broker each; use1-az3 is excluded because MSK can't place brokers there."
  type        = map(number)
  default = {
    "use1-az1" = 0
    "use1-az2" = 1
    "use1-az4" = 2
  }

  validation {
    condition     = length(var.az_slots) == 3
    error_message = "Provide exactly three AZs (one per MSK broker)."
  }

  # Slots 0-5 fit the layout: data /24s use 10.40.10-15.x, below the first app
  # /20 at 10.40.16.0. Each AZ needs its own slot.
  validation {
    condition = (
      alltrue([for slot in values(var.az_slots) : slot >= 0 && slot <= 5 && floor(slot) == slot]) &&
      length(distinct(values(var.az_slots))) == length(var.az_slots)
    )
    error_message = "Each AZ needs a different whole-number slot between 0 and 5."
  }
}

variable "flow_log_retention_days" {
  description = "How long VPC flow logs are kept in CloudWatch Logs."
  type        = number
  default     = 14
}
