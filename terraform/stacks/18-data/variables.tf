# -----------------------------------------------------------------------------
# 18-data/variables.tf - the data buckets and how long each keeps objects.
#
# What: one entry per bucket, with an optional expiry for current objects.
# Why:  retention is a data decision, not just a cost one:
#   - datasets, artefacts: kept forever. Every model must be traceable to the
#     exact dataset version and artefact it came from (lineage), and DR
#     redeploys "the last known-good model" from here.
#   - scored: the archive of every scored event. Retraining uses the most recent
#     labelled window, and DR replays from it, so 90 days is plenty.
#   - capture: SageMaker endpoint data capture, read hourly by Model Monitor.
#     Only recent data matters for drift, so 30 days.
# Versioning keeps overwritten/deleted objects for noncurrent_days, so a bad
# write (or a bad script) can be rolled back.
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

variable "buckets" {
  description = "Data buckets: purpose and optional expiry (days) for current objects; null = keep forever."
  type = map(object({
    purpose     = string
    expire_days = optional(number)
  }))
  default = {
    datasets = {
      purpose = "Versioned training datasets - transactions/vN with manifests"
    }
    artefacts = {
      purpose = "Model artefacts with metrics and model cards and reference tables"
    }
    scored = {
      purpose     = "Archive of every scored event - raw fields plus features and score and model version"
      expire_days = 90
    }
    capture = {
      purpose     = "SageMaker endpoint data capture read by Model Monitor"
      expire_days = 30
    }
  }
}

variable "noncurrent_days" {
  description = "How long overwritten or deleted object versions are kept."
  type        = number
  default     = 30
}
