# -----------------------------------------------------------------------------
# bootstrap/github-oidc.tf - lets GitHub Actions use AWS without stored keys.
#
# What: the GitHub OIDC identity provider plus three roles, each trusted only
#       from one kind of workflow run:
#         plan     - pull requests            -> read-only + state read/lock
#         apply    - "production"/"teardown"   -> admin (apply and nightly destroy)
#                    GitHub environments
#         ecr-push - pushes to main           -> push images to fs/* repos only
# Why:  short-lived credentials per run instead of long-lived access keys in
#       GitHub secrets. The trust policy's "sub" condition decides WHICH runs
#       get WHICH role, so a pull request (which anyone could open on a public
#       repo) can only ever plan, never apply.
# Concept: the "production" environment has a required reviewer (you), so every
#       apply waits for a click; "teardown" has none, so the nightly destroy is
#       never blocked. Both are set up by scripts/setup-github.sh.
# Reused from clinical-trial-matching (Helm/ECR Public statement dropped -
# fraudstream has no Helm charts).
# -----------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn

  # GitHub's OIDC subject includes immutable owner/repo IDs, which protects
  # against repo renames and repojacking (same format as clinical-trial-matching).
  github_oidc_subject_prefix = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}"
}

# -----------------------------------------------------------------------------
# PLAN role - read-only, assumable only from pull requests in this repo
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "github_plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_oidc_subject_prefix}:pull_request"]
    }
  }
}

resource "aws_iam_role" "github_plan" {
  name                 = "${var.project_name}-github-plan"
  description          = "Assumed by GitHub Actions on pull requests to run terraform plan"
  assume_role_policy   = data.aws_iam_policy_document.github_plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "github_plan_readonly" {
  role       = aws_iam_role.github_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# Plan needs to read state and create/remove the S3 lock file - nothing more.
data "aws_iam_policy_document" "github_plan_state" {
  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/*"]
  }

  statement {
    sid       = "ManageLockFiles"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/*.tflock"]
  }
}

resource "aws_iam_role_policy" "github_plan_state" {
  name   = "terraform-state-access"
  role   = aws_iam_role.github_plan.id
  policy = data.aws_iam_policy_document.github_plan_state.json
}

# -----------------------------------------------------------------------------
# APPLY role - assumable only from the "production" and "teardown" GitHub
# environments; production requires manual approval before any apply runs.
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "github_apply_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "${local.github_oidc_subject_prefix}:environment:production",
        "${local.github_oidc_subject_prefix}:environment:teardown",
      ]
    }
  }
}

resource "aws_iam_role" "github_apply" {
  name                 = "${var.project_name}-github-apply"
  description          = "Assumed by GitHub Actions in the production/teardown environments to apply and destroy stacks"
  assume_role_policy   = data.aws_iam_policy_document.github_apply_trust.json
  max_session_duration = 3600
}

# Broad on purpose for now: the apply role will create VPCs, MSK, SageMaker,
# ECS and IAM roles. The account boundary limits the blast radius - this role
# can only touch fraudstream-dev. TODO: scope down with a permissions boundary
# once the stack set is stable (a good interview follow-up).
resource "aws_iam_role_policy_attachment" "github_apply_admin" {
  #checkov:skip=CKV_AWS_274:Apply role needs broad permissions for now; confined to a dedicated account, to be scoped with a permissions boundary later
  role       = aws_iam_role.github_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# -----------------------------------------------------------------------------
# ECR PUSH role - assumable only from pushes to main. Publishes images to
# fs/* repositories, nothing else. First used in Phase 2 (simulator/consumer).
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "github_ecr_push_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_oidc_subject_prefix}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_ecr_push" {
  name                 = "${var.project_name}-github-ecr-push"
  description          = "Assumed by GitHub Actions on main to push images to ECR"
  assume_role_policy   = data.aws_iam_policy_document.github_ecr_push_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_ecr_push" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # account-level API, cannot be scoped
  }

  statement {
    sid = "PushToProjectRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${var.project_name}/*"]
  }
}

resource "aws_iam_role_policy" "github_ecr_push" {
  name   = "ecr-push"
  role   = aws_iam_role.github_ecr_push.id
  policy = data.aws_iam_policy_document.github_ecr_push.json
}
