# -----------------------------------------------------------------------------
# modules/s3-bucket/variables.tf - what varies between data buckets: the name,
# what it's for, and how long it keeps objects. Everything else is baseline.
# -----------------------------------------------------------------------------
variable "name" {
  description = "Globally unique bucket name."
  type        = string
}

variable "purpose" {
  description = "What the bucket holds; stored as the Purpose tag."
  type        = string
}

variable "expire_days" {
  description = "Delete current objects after this many days; null keeps them forever."
  type        = number
  default     = null
}

variable "noncurrent_days" {
  description = "How long overwritten or deleted object versions are kept."
  type        = number
  default     = 30
}
