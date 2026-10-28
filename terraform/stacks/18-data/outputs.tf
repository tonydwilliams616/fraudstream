# -----------------------------------------------------------------------------
# 18-data/outputs.tf - bucket names and ARNs, read by later stacks (19-ml roles,
# 35-apps consumer, 40-serving data capture) via terraform_remote_state, and by
# the dataset script in session 2.
# -----------------------------------------------------------------------------
output "bucket_names" {
  description = "Bucket names by short name (datasets, artefacts, scored, capture)."
  value       = { for k, b in aws_s3_bucket.this : k => b.id }
}

output "bucket_arns" {
  description = "Bucket ARNs by short name - for IAM policies in later stacks."
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}
