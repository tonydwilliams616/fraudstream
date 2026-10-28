# bootstrap – first-time setup runbook

The one stack that is **applied by hand** rather than by GitHub Actions. It creates the
two things the pipelines need before they can run at all: the Terraform **state bucket**
and the **GitHub OIDC roles** the workflows assume. After this, everything else is
deployed by pull request.

What it creates (all persistent, ~pennies/month):

| Resource | Purpose |
| --- | --- |
| `fs-tfstate-831547420318` | S3 bucket holding state for every stack (one key per stack) |
| GitHub OIDC provider | Lets GitHub Actions exchange its job token for AWS credentials |
| `fs-github-plan` | Read-only; trusted only from **pull requests** → `terraform plan` |
| `fs-github-apply` | Admin; trusted only from the **production** and **teardown** environments |
| `fs-github-ecr-push` | Push to `fs/*` ECR repos; trusted only from **main** (Phase 2) |

## Steps (session 1)

Run everything from the repo root in a terminal where you've run `source ./aws-login.sh`.

### 1. Put the repo's numeric ID into `variables.tf`

```bash
gh api repos/tonydwilliams616/fraudstream --jq .id
```

Paste the number as the default of `github_repo_id` in `variables.tf`. It's not secret –
it goes into the OIDC trust policy so that only *this* repo (even if renamed) can assume
the roles.

### 2. Apply with local state, then move the state into S3

```bash
cd terraform/bootstrap
terraform init
terraform apply                      # review: ~20 resources, all IAM/S3

cp backend.tf.example backend.tf
terraform init -migrate-state        # answer "yes": local state -> S3
rm terraform.tfstate terraform.tfstate.backup   # now safely in S3 (gitignored anyway)
cd ../..
```

### 3. Configure the GitHub repo

```bash
gh auth status              # log in with `gh auth login` if needed
./scripts/setup-github.sh
```

Sets the role ARNs as repository variables, `ALERT_EMAIL` as a secret, the
`production` (approval required) and `teardown` (no approval) environments, and branch
protection on `main` requiring the `ci-passed` check.

> Environment reviewers and branch protection need the repo to be **public** (or a paid
> GitHub plan). If the script errors on step 3 or 4, that's why.

### 4. Decide on cost anomaly detection

```bash
aws ce get-anomaly-monitors --query 'AnomalyMonitors[].MonitorName'
```

Empty list → set `enable_anomaly_detection` default to `true` in
`terraform/stacks/00-account/variables.tf`. A default monitor already exists → leave it
`false` (AWS allows only one service monitor per account).

### 5. First pull request

```bash
git switch -c session-1-foundations
git add -A && git commit -m "Session 1: bootstrap, guardrails, registry, data buckets, CI/CD"
git push -u origin session-1-foundations
gh pr create --fill
```

On the PR you should see **Terraform CI** (fmt, validate, tflint, Checkov → `ci-passed`)
and **Terraform Deploy** posting one plan comment each for `00-account`, `05-registry`
and `18-data`. Merge it, then approve the `apply` job under **Actions**.

Afterwards, **click the two SNS confirmation emails** (budget alerts and ops alerts) –
nothing is delivered until you do.

### 6. Prove the teardown works

Actions → **Terraform Destroy** → Run workflow → type `destroy`. With no ephemeral stacks
yet it should report *nothing to destroy* and the **Verify** step should pass. From now
on it runs every night at 22:47 UTC.

## If a workflow can't assume a role

`Not authorized to perform sts:AssumeRoleWithWebIdentity` almost always means the token's
`sub` claim doesn't match the trust policy. The expected values are
`repo:tonydwilliams616@219661695/fraudstream@<repo-id>:pull_request` (plan),
`...:environment:production` / `...:environment:teardown` (apply) and
`...:ref:refs/heads/main` (ECR push). Check `github_repo_id` first.
