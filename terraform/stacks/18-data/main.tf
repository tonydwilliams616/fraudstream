# -----------------------------------------------------------------------------
# 18-data/main.tf - the four S3 buckets the ML lifecycle runs on.
#
# What: fs-<name>-831547420318 for datasets, artefacts, scored and capture,
#       each built by modules/s3-bucket (private, versioned, encrypted,
#       TLS-only, lifecycle rules, prevent_destroy).
# Why:  these hold everything that must outlive the nightly teardown: the data
#       a model was trained on, the model itself, and the events it scored.
# Concept: S3 is the system of record, Kafka is not. MSK is destroyed every
#       night, so anything needed later (retraining, DR replay) must already be
#       in the scored bucket.
# Stack type: PERSISTENT. Cost: $0.023/GB-month - pennies at this project's size.
# -----------------------------------------------------------------------------
module "bucket" {
  source   = "../../modules/s3-bucket"
  for_each = var.buckets

  name            = "${var.project_name}-${each.key}-${var.aws_account_id}"
  purpose         = each.value.purpose
  expire_days     = each.value.expire_days
  noncurrent_days = var.noncurrent_days
}
