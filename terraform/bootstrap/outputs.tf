# -----------------------------------------------------------------------------
# bootstrap/outputs.tf - values the rest of the setup needs.
#
# What: the state bucket name and the three role ARNs.
# Why:  scripts/setup-github.sh reads these outputs and stores the role ARNs as
#       GitHub repository variables, so the workflows can assume the roles.
#       Role ARNs aren't secret (they're useless without a matching OIDC token),
#       which is why they're variables, not secrets.
# -----------------------------------------------------------------------------
output "state_bucket_name" {
  description = "S3 bucket holding Terraform state for all stacks."
  value       = aws_s3_bucket.tfstate.id
}

output "github_plan_role_arn" {
  description = "Role for terraform plan on pull requests - GitHub variable AWS_PLAN_ROLE_ARN."
  value       = aws_iam_role.github_plan.arn
}

output "github_apply_role_arn" {
  description = "Role for apply/destroy from the production and teardown environments - GitHub variable AWS_APPLY_ROLE_ARN."
  value       = aws_iam_role.github_apply.arn
}

output "github_ecr_push_role_arn" {
  description = "Role for pushing images from main - GitHub variable AWS_ECR_PUSH_ROLE_ARN."
  value       = aws_iam_role.github_ecr_push.arn
}
