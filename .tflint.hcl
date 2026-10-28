# -----------------------------------------------------------------------------
# .tflint.hcl - linter config used by the Terraform CI workflow.
#
# What: Terraform's "recommended" rules (unused declarations, missing version
#       constraints, naming) plus the AWS ruleset (invalid instance types,
#       deprecated arguments and similar mistakes `terraform validate` misses).
# Why:  validate only checks syntax and types; tflint catches the mistakes that
#       would otherwise surface halfway through an apply.
# -----------------------------------------------------------------------------
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.50.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
