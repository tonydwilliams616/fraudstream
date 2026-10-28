# -----------------------------------------------------------------------------
# bootstrap/variables.tf - inputs for the bootstrap stack.
#
# What: names, the account ID, and the GitHub identity the OIDC roles trust.
# Why:  github_owner_id / github_repo_id are GitHub's immutable numeric IDs.
#       They go into the OIDC trust condition, so if the repo is ever renamed
#       or deleted and someone recreates one with the same name ("repojacking"),
#       their workflows still can't assume these roles.
# Reused from clinical-trial-matching, with fraudstream values.
# -----------------------------------------------------------------------------
variable "project_name" {
  description = "Short prefix used for naming resources (fs = fraudstream)."
  type        = string
  default     = "fs"
}

variable "aws_region" {
  description = "AWS region for the state bucket and all stacks."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "The fraudstream-dev account ID. The provider refuses to run against any other account."
  type        = string
  default     = "831547420318"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Set aws_account_id to the 12-digit fraudstream-dev account ID."
  }
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the repo."
  type        = string
  default     = "tonydwilliams616"
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
  default     = "fraudstream"
}

variable "github_owner_id" {
  description = "Immutable numeric ID of the GitHub owner, included in the OIDC sub claim."
  type        = string
  default     = "219661695" # same owner as clinical-trial-matching
}

variable "github_repo_id" {
  description = "Immutable numeric ID of the fraudstream repo, included in the OIDC sub claim."
  type        = string
  # From: gh api repos/tonydwilliams616/fraudstream --jq .id
  default = "1435006720"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repo_id))
    error_message = "Set github_repo_id to the repo's numeric ID (gh api repos/tonydwilliams616/fraudstream --jq .id)."
  }
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if one already exists in this account."
  type        = bool
  default     = true # new account: there isn't one yet
}
