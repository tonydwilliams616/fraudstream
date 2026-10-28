# fraudstream

Real-time card-fraud scoring that keeps itself healthy. Transactions stream through
Kafka (Amazon MSK), are scored by an XGBoost model on a SageMaker real-time endpoint,
and data drift triggers a gated retrain and a blue/green promotion. Everything is
Terraform, delivered by GitHub Actions, and torn down nightly to control cost.

The focus is the **ML lifecycle** – data versioning, evaluation gates, safe deployment,
drift detection, retraining, DR – not model accuracy.

## Status

Phase 1 (foundations, data and baseline model) – in progress. The full plan (phases,
decisions, costs) is kept as `fraudstream-plan.md` in the Claude project and copied to
`docs/` at the end of each phase.

## Repository layout

```
terraform/
  bootstrap/           applied by hand once: state bucket + GitHub OIDC roles (see its README)
  modules/
    cost-guardrails/   budgets, alerts, anomaly detection (reused from clinical-trial-matching)
  stacks/              one state file per stack, applied in numeric order
    00-account/        P  budgets + ops-alerts SNS topic
    05-registry/       P  ECR repositories (simulator, consumer)
    18-data/           P  S3: datasets, artefacts, scored archive, endpoint data capture
.github/workflows/
  terraform-ci.yml       fmt, validate, tflint, Checkov -> single "ci-passed" check
  terraform-deploy.yml   plan on PR (comment), approval-gated apply on main, one-click rebuild
  terraform-destroy.yml  nightly teardown of ephemeral stacks + SageMaker sweep + verify
scripts/
  setup-github.sh      repo variables, secret, environments, branch protection
```

**P** = persistent (kept by the nightly teardown). Ephemeral stacks (NAT, MSK, ECS
services, the SageMaker endpoint, Grafana) arrive in Phase 2 and are listed in
`EPHEMERAL_STACKS` in `terraform-destroy.yml`.

## Account

Runs in its own AWS Organizations member account (`fraudstream-dev`, non-production OU),
so the account's bill *is* the project's cost and its blast radius is contained. Every
provider block sets `allowed_account_ids`, so Terraform refuses to run against any other
account.

## Getting started

First-time setup: `terraform/bootstrap/README.md`.
Day to day: `source ./aws-login.sh` (local, gitignored) for AWS access in your terminal.
