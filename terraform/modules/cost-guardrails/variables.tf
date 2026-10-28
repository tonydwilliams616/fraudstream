# -----------------------------------------------------------------------------
# modules/cost-guardrails/variables.tf - inputs for the cost-guardrails module.
#
# What: budget limits, alert thresholds and which optional pieces to create.
# Why:  the same module serves an account shared by several projects (tag-scoped
#       project budget) or a dedicated account like fraudstream-dev (account
#       budget only - project_tag_value = null).
# Reused verbatim from clinical-trial-matching.
# -----------------------------------------------------------------------------
variable "project_name" {
  description = "Prefix used for naming all resources."
  type        = string
}

variable "alert_email" {
  description = "Email address that receives budget and anomaly alerts."
  type        = string
}

variable "monthly_budget_limit" {
  description = "Account-wide monthly budget in USD."
  type        = number
  default     = 50
}

variable "project_budget_limit" {
  description = "Monthly budget in USD for resources carrying the project tag."
  type        = number
  default     = 40
}

variable "project_tag_key" {
  description = "Cost allocation tag key used to scope the project budget."
  type        = string
  default     = "Project"
}

variable "project_tag_value" {
  description = "Tag value to scope the project budget to. Set to null to skip the project budget."
  type        = string
  default     = null
}

variable "activate_cost_allocation_tag" {
  description = "Activate the project tag as a cost allocation tag. Only works once the tag has appeared in billing data (can take ~24h after first tagged resource)."
  type        = bool
  default     = false
}

variable "alert_thresholds" {
  description = "Budget notification thresholds (percentage) and whether they fire on ACTUAL or FORECASTED spend."
  type = list(object({
    threshold = number
    type      = string
  }))
  default = [
    { threshold = 50, type = "ACTUAL" },
    { threshold = 80, type = "ACTUAL" },
    { threshold = 100, type = "ACTUAL" },
    { threshold = 100, type = "FORECASTED" },
  ]

  validation {
    condition     = alltrue([for t in var.alert_thresholds : contains(["ACTUAL", "FORECASTED"], t.type)])
    error_message = "Each threshold type must be ACTUAL or FORECASTED."
  }
}

variable "enable_anomaly_detection" {
  description = "Create a Cost Anomaly Detection monitor and daily email subscription."
  type        = bool
  default     = true
}

variable "anomaly_threshold_usd" {
  description = "Minimum total anomaly impact in USD before you get alerted."
  type        = number
  default     = 10
}

variable "tags" {
  description = "Tags applied to taggable resources."
  type        = map(string)
  default     = {}
}
