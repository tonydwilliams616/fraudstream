# -----------------------------------------------------------------------------
# bootstrap/state-bucket.tf - the S3 bucket that holds Terraform state for
# every stack in this repo (one key per stack, e.g. 00-account/terraform.tfstate).
#
# What: a versioned, encrypted, private bucket with a TLS-only policy.
# Why:  state is the source of truth for what Terraform owns, and it can hold
#       sensitive values, so it gets versioning (roll back a bad write),
#       encryption, no public access and prevent_destroy.
# Concept: S3 native locking (use_lockfile, Terraform >= 1.10) writes a .tflock
#       object next to the state - no DynamoDB lock table needed any more.
# Cost: pennies a month. Persistent - never destroyed by the nightly teardown.
# Reused from clinical-trial-matching (bucket becomes fs-tfstate-831547420318).
# -----------------------------------------------------------------------------
data "aws_caller_identity" "current" {}

locals {
  state_bucket_name = "${var.project_name}-tfstate-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "tfstate" {
  #checkov:skip=CKV_AWS_144:Cross-region replication not needed for Terraform state; it can be rebuilt by importing
  #checkov:skip=CKV_AWS_18:Access logging not needed for a single-user state bucket
  #checkov:skip=CKV2_AWS_62:Event notifications not needed for a state bucket
  #checkov:skip=CKV_AWS_145:SSE-S3 is sufficient here; avoids KMS key cost
  bucket = local.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "tfstate" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.tfstate.arn,
      "${aws_s3_bucket.tfstate.arn}/*",
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

resource "aws_s3_bucket_policy" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  policy = data.aws_iam_policy_document.tfstate.json

  depends_on = [aws_s3_bucket_public_access_block.tfstate]
}
