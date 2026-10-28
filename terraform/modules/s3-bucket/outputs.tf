# -----------------------------------------------------------------------------
# modules/s3-bucket/outputs.tf - the bucket's name and ARN, for the caller's
# outputs and for IAM policies in later stacks.
# -----------------------------------------------------------------------------
output "id" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.id
}

output "arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.this.arn
}
