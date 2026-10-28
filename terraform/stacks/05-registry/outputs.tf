# -----------------------------------------------------------------------------
# 05-registry/outputs.tf - repository URLs (for the image workflow) and ARNs
# (for IAM policies in later stacks, e.g. ECS task execution roles).
# -----------------------------------------------------------------------------
output "repository_urls" {
  description = "Repository URLs by short name - used by image CI and ECS task definitions."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "repository_arns" {
  description = "Repository ARNs by short name - for IAM policies in later stacks."
  value       = { for k, r in aws_ecr_repository.this : k => r.arn }
}
