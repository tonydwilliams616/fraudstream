# -----------------------------------------------------------------------------
# 18-data/main.tf - the four S3 buckets the ML lifecycle runs on.
#
# What: fs-<name>-831547420318 for datasets, artefacts, scored and capture,
#       all private, versioned, encrypted, TLS-only, with lifecycle rules.
# Why:  these hold everything that must outlive the nightly teardown: the data
#       a model was trained on, the model itself, and the events it scored.
#       prevent_destroy stops a stray `terraform destroy` (or a mistake in the
#       teardown list) from deleting them.
# Concept: S3 is the system of record, Kafka is not. MSK is destroyed every
#       night, so anything needed later (retraining, DR replay) must already be
#       in the scored bucket.
# Stack type: PERSISTENT. Cost: $0.023/GB-month - pennies at this project's size.
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is added in Phase 7 (DR) for datasets, artefacts and scored
  #checkov:skip=CKV_AWS_18:Access logging not needed for a single-user portfolio project; CloudTrail covers management events
  #checkov:skip=CKV2_AWS_62:Event notifications not needed yet; the drift trigger uses CloudWatch/EventBridge, not S3 events
  #checkov:skip=CKV_AWS_145:SSE-S3 is sufficient for synthetic data; avoids KMS key and request costs
  for_each = var.buckets

  bucket = "${var.project_name}-${each.key}-${var.aws_account_id}"

  tags = {
    Purpose = each.value.purpose
  }

  lifecycle {
    prevent_destroy = true # data outlives every teardown
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled" # also a prerequisite for cross-region replication (Phase 7)
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    object_ownership = "BucketOwnerEnforced" # no ACLs; access is IAM/bucket policy only
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Only for buckets with a retention period (scored, capture)
  dynamic "rule" {
    for_each = var.buckets[each.key].expire_days == null ? [] : [var.buckets[each.key].expire_days]
    content {
      id     = "expire-current-objects"
      status = "Enabled"

      filter {}

      expiration {
        days = rule.value
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      each.value.arn,
      "${each.value.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  policy = data.aws_iam_policy_document.tls_only[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.this]
}
