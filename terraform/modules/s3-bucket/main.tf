# -----------------------------------------------------------------------------
# modules/s3-bucket/main.tf - one private data bucket with the project's
# baseline settings, so every data bucket is configured the same way.
#
# What: the bucket plus versioning, SSE-S3 encryption, a public-access block,
#       BucketOwnerEnforced ownership, lifecycle rules and a TLS-only policy.
# Why a module: 18-data creates several buckets. Doing that with a for_each on
#       each resource works in Terraform, but static scanners like Checkov
#       can't follow a for_each that iterates over another for_each - it linked
#       the settings to only one bucket and reported the rest as unversioned and
#       public (false positives). Inside this module there's ONE bucket and no
#       for_each, so every reference is explicit; the caller does the for_each.
#       It also means a new data bucket can't be created without the baseline,
#       and Phase 7's cross-region replication gets added in one place.
# prevent_destroy: data outlives every teardown. Terraform refuses to plan a
#       destroy of any bucket created from this module.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is added in Phase 7 (DR) for datasets, artefacts and scored
  #checkov:skip=CKV_AWS_18:Access logging not needed for a single-user portfolio project; CloudTrail covers management events
  #checkov:skip=CKV2_AWS_62:Event notifications not needed yet; the drift trigger uses CloudWatch/EventBridge, not S3 events
  #checkov:skip=CKV_AWS_145:SSE-S3 is sufficient for synthetic data; avoids KMS key and request costs
  bucket = var.name

  tags = {
    Purpose = var.purpose
  }

  lifecycle {
    prevent_destroy = true # data outlives every teardown
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled" # also a prerequisite for cross-region replication (Phase 7)
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced" # no ACLs; access is IAM/bucket policy only
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

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

  # Only for buckets with a retention period (e.g. scored, capture)
  dynamic "rule" {
    for_each = var.expire_days == null ? [] : [var.expire_days]
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
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this.arn,
      "${aws_s3_bucket.this.arn}/*",
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
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.this]
}
