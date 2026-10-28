#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scripts/setup-github.sh - one-off GitHub repo setup, run after the bootstrap
# apply. Safe to re-run: every step is a "set", not an "add".
#
# What it sets on tonydwilliams616/fraudstream:
#   1. Repository VARIABLES  AWS_REGION, AWS_PLAN_ROLE_ARN, AWS_APPLY_ROLE_ARN,
#                            AWS_ECR_PUSH_ROLE_ARN (read from bootstrap outputs)
#   2. Repository SECRET     ALERT_EMAIL (prompted; becomes TF_VAR_alert_email)
#   3. ENVIRONMENTS          production - you must approve every apply;
#                                         deployable from main only
#                            teardown   - no approval (the nightly destroy
#                                         must never wait); main only
#   4. BRANCH PROTECTION     main: changes via pull request, the "ci-passed"
#                            check must pass, no force-push or deletion
#
# Why variables vs secrets: role ARNs aren't secret - they're useless without a
# matching OIDC token - so they're variables you can read in logs. The email
# address is personal, so it's a secret.
# Why environments matter for security: the apply role's IAM trust policy only
# accepts tokens from these two environments, and an environment restricted to
# main means a workflow on a feature branch can't get that token at all.
#
# Needs: gh (logged in: gh auth login), terraform, and AWS credentials for
# fraudstream-dev (source ./aws-login.sh) so bootstrap outputs can be read.
# Usage: ./scripts/setup-github.sh            (from the repo root)
# -----------------------------------------------------------------------------
set -euo pipefail

REPO="tonydwilliams616/fraudstream"
REGION="us-east-1"
BOOTSTRAP_DIR="terraform/bootstrap"

out() { terraform -chdir="$BOOTSTRAP_DIR" output -raw "$1"; }

echo "==> Reading bootstrap outputs"
PLAN_ROLE=$(out github_plan_role_arn)
APPLY_ROLE=$(out github_apply_role_arn)
ECR_ROLE=$(out github_ecr_push_role_arn)

echo "==> 1. Repository variables"
gh variable set AWS_REGION            --repo "$REPO" --body "$REGION"
gh variable set AWS_PLAN_ROLE_ARN     --repo "$REPO" --body "$PLAN_ROLE"
gh variable set AWS_APPLY_ROLE_ARN    --repo "$REPO" --body "$APPLY_ROLE"
gh variable set AWS_ECR_PUSH_ROLE_ARN --repo "$REPO" --body "$ECR_ROLE"

echo "==> 2. Secret ALERT_EMAIL (receives budget, anomaly and ops alerts)"
if gh secret list --repo "$REPO" | grep -q '^ALERT_EMAIL'; then
  read -r -p "ALERT_EMAIL already set. Replace it? [y/N] " ans
else
  ans=y
fi
if [[ "$ans" =~ ^[Yy]$ ]]; then
  read -r -p "Alert email address: " email
  gh secret set ALERT_EMAIL --repo "$REPO" --body "$email"
fi

echo "==> 3. Environments"
MY_ID=$(gh api user --jq .id)

# production: you are the required reviewer. prevent_self_review=false because
# you're also the person who merges - otherwise nobody could approve.
gh api --method PUT "repos/$REPO/environments/production" --input - <<EOF
{
  "reviewers": [{ "type": "User", "id": $MY_ID }],
  "prevent_self_review": false,
  "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true }
}
EOF

# teardown: no reviewers, so the scheduled run is never blocked.
gh api --method PUT "repos/$REPO/environments/teardown" --input - <<'EOF'
{
  "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true }
}
EOF

# Both environments: deployable from main only (ignore "already exists" on re-runs)
for env in production teardown; do
  gh api --method POST "repos/$REPO/environments/$env/deployment-branch-policies" \
    -f name=main -f type=branch > /dev/null 2>&1 || true
done

echo "==> 4. Branch protection on main"
# 0 required approvals: you're the only developer, so the PR is for the plan
# comment and the CI checks, not for a second reviewer. enforce_admins=false
# leaves you a way out if CI itself breaks.
gh api --method PUT "repos/$REPO/branches/main/protection" --input - <<'EOF' > /dev/null
{
  "required_status_checks": { "strict": true, "checks": [{ "context": "ci-passed" }] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0, "dismiss_stale_reviews": true },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
EOF

echo
echo "Done. Check: https://github.com/$REPO/settings/environments and .../settings/branches"
