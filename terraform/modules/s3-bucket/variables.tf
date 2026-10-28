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

  # S3 rejects tag values with other characters (e.g. commas, brackets) only at
  # apply time - plan, validate and Checkov don't call the API - so check here
  # to fail at plan time instead.
  validation {
    condition     = can(regex("^[a-zA-Z0-9 _.:/=+@-]{0,256}$", var.purpose))
    error_message = "purpose may only contain letters, numbers, spaces and _ . : / = + - @ (S3 tag rules), max 256 characters."
  }
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
