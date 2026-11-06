# fraudstream

Real-time card-fraud scoring that keeps itself healthy. Transactions stream through
Kafka (Amazon MSK), are scored by an XGBoost model on a SageMaker real-time endpoint,
and data drift triggers a gated retrain and a blue/green promotion. Everything is
Terraform, delivered by GitHub Actions, and torn down nightly to control cost.

The focus is the **ML lifecycle** – data versioning, evaluation gates, safe deployment,
drift detection, retraining, DR – not model accuracy.

## Status

Phase 1 (foundations, data and baseline model) – complete. Phase 2 (streaming and serving) –
in progress.

### Phase 1 – data and baseline model

- **Data:** `transactions/v2` – 30 days, 10,000 cards, 506,357 transactions, 0.49% fraud in
  three styles (card testing, account takeover, online fraud). Rebuilt exactly from seed 42;
  table fingerprints are in its manifest. (v1 is kept unchanged; v2 fixed fraud tailing off at
  the end of the period.)
- **Features:** 8 per-transaction and 6 card-history features (schema v1), calculated by one
  `FeatureBuilder` for both training and live scoring. Tests guard against leakage and
  training-serving skew.
- **Baseline model:** XGBoost, split by time (days 1–21 train, 22–25 validation, 26–30 test),
  alert threshold chosen on validation for a 0.5% alert budget.
- **Test results** (XGBoost 3.0.5, 117 trees): PR-AUC 0.795 (95% range 0.760–0.830; random
  0.005), ~79 alerts/day, precision 0.77, recall 0.72. Recall by style: card testing 0.90,
  account takeover 0.52, online fraud 0.43.
- **Reproduce:** `python scripts/data/make_dataset.py --version v2 --seed 42`, then
  `python scripts/train/train_baseline.py` and `python scripts/train/package_model.py <run>`.

## Repository layout

```
terraform/
  bootstrap/           applied by hand once: state bucket + GitHub OIDC roles (see its README)
  modules/
    cost-guardrails/   budgets, alerts, anomaly detection
  stacks/              one state file per stack, applied in numeric order
    00-account/        P  budgets + ops-alerts SNS topic
    05-registry/       P  ECR repositories (simulator, consumer)
    10-network/        P  VPC over 3 AZs: public/app/data subnets, S3 endpoint, flow logs, security groups
    15-nat/            E  NAT gateway per AZ + default routes for the app subnets
    18-data/           P  S3: datasets, artefacts, scored archive, endpoint data capture
    19-ml/             P  IAM roles (endpoint, ECS tasks), model registry group
.github/workflows/
  terraform-ci.yml       fmt, validate, tflint, Checkov -> single "ci-passed" check
  python-ci.yml          ruff + pytest -> single "python-ci-passed" check
  terraform-deploy.yml   plan on PR (comment), approval-gated apply on main, one-click rebuild
  terraform-destroy.yml  nightly teardown of ephemeral stacks + SageMaker sweep + verify
scripts/
  setup-github.sh      repo variables, secret, environments, branch protection
  data/make_dataset.py generate a dataset version -> data/ and s3://fs-datasets-.../
  train/train_baseline.py  train + evaluate the baseline model -> models/<run-id>/
  train/package_model.py   model card + SageMaker model.tar.gz -> s3://fs-artefacts-.../
src/fraudstream/       the Python package (pip install -e ".[dev]")
  generator/           synthetic cardholders, shops, transactions and fraud, from one seed
  features/            FeatureBuilder: the one place features are calculated
  storage.py           immutable dataset versions: Parquet + manifest, local and S3
  training/            time split, XGBoost training, evaluation at an alert budget
tests/                 pytest: generator, features, leakage, skew, reproducibility
pyproject.toml         package definition, dependencies, pytest and ruff settings
```

**P** = persistent (kept by the nightly teardown). **E** = ephemeral: billed by the hour,
destroyed every night at 01:47 UTC and rebuilt with one run of Terraform Deploy (stack = all).
Ephemeral stacks are listed in `EPHEMERAL_STACKS` in `terraform-destroy.yml`.

## Account

Runs in its own AWS Organizations member account (`fraudstream-dev`, non-production OU),
so the account's bill *is* the project's cost and its blast radius is contained. Every
provider block sets `allowed_account_ids`, so Terraform refuses to run against any other
account.

## Getting started

First-time setup: `terraform/bootstrap/README.md`.
Day to day: `source ./aws-login.sh` (local, gitignored) for AWS access in your terminal.
