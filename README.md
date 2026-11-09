# fraudstream

Real-time card-fraud scoring that keeps itself healthy. Transactions stream through
Kafka (Amazon MSK), are scored by an XGBoost model on a SageMaker real-time endpoint,
and data drift triggers a gated retrain and a blue/green promotion. Everything is
Terraform, delivered by GitHub Actions, and torn down nightly to control cost.

The focus is the **ML lifecycle** – data versioning, evaluation gates, safe deployment,
drift detection, retraining, DR – not model accuracy.

## Status

Phase 1 (foundations, data and baseline model) – complete. Phase 2 (streaming and serving) –
complete. Next: Phase 3 (training pipeline and evaluation gate).

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

### Phase 2 – streaming and serving

```
simulator (Fargate) --transactions--> MSK --> consumer (Fargate) --CSV micro-batch--> SageMaker endpoint fs-fraud
                                               |                                       (model package v1, data capture)
                                               +--> scored / fraud.alerts topics
                                               +--> S3 archive scored/dt=/hour=/ (1 file a minute)
```

- **Streaming:** Amazon MSK, 3 brokers (kafka.m7g.large, one per AZ, KRaft), replication 3,
  `min.insync.replicas` 2, producers `acks=all` + idempotence, IAM auth over TLS only. Messages
  keyed by card, so each card's transactions stay in order on one partition.
- **Simulator:** streams new days for the training data's cardholders and shops at 10/s,
  with no labels on the wire (a bank learns about fraud later).
- **Model registry and serving:** the Phase 1 baseline registered as version 1 of
  `fs-fraud-detection` (container, weights, lineage, threshold and metrics recorded) and
  approved. The endpoint is built from the approved package; which version is served is one
  line in `terraform/stacks/40-serving/model.auto.tfvars`, so a promotion is a reviewed commit
  and a rollback is a revert. Data capture records every request and score for drift
  monitoring.
- **Consumer:** finds the deployed model through the registry (threshold and feature contract
  from the package; refuses an unapproved model or one expecting different features), builds
  features with the same `FeatureBuilder` as training, scores 50-transaction micro-batches in
  one endpoint call, and writes every scored record to Kafka and S3 with its model version,
  Kafka position and latency. Offsets are committed only after Kafka has confirmed the results
  and the S3 file is written (at-least-once); partition hand-overs checkpoint first.
- **Images:** ARM64, Debian 13 with OS patches, unprivileged user, read-only root filesystem,
  pinned by digest in the task definitions, Trivy-scanned in CI before push
  (`docs/security.md`).

**Measured (November 2026):**

| Check | Result |
|---|---|
| Endpoint vs local model (training-serving parity) | 3,421 test-day rows: max difference 3e-8 (float32 rounding), 0 alert decisions changed |
| End-to-end latency (produce → scored), steady state | p50 155 ms (waiting up to 200 ms for a micro-batch, plus one endpoint call) |
| Consumer killed mid-stream, then scaled 1 → 2 → 1 | 8,009 transactions over 14 minutes: 0 lost, 0 scored twice |
| Recovery | replacement task scoring again after ~100 s; hand-over between two running consumers 1.6 s |
| Full rebuild from nothing (one workflow run) | 43 minutes (mostly MSK and the endpoint) |
| Alert rate | 0.5–0.7% over a ~1-hour run (threshold set for a 0.5% budget); lower in short windows, as fraud arrives in bursts |

**Check it yourself** (platform up, `source ./aws-login.sh`):
`python scripts/serve/check_endpoint.py baseline-20261106T120445Z` (parity) and
`python scripts/ops/check_scoring.py --minutes 15` (no gaps, duplicates, latency).

## Repository layout

```
terraform/
  bootstrap/           applied by hand once: state bucket + GitHub OIDC roles (see its README)
  modules/
    cost-guardrails/   budgets, alerts, anomaly detection
    s3-bucket/         one data bucket with the baseline settings
    ecs-service/       one app on Fargate: digest-pinned image, read-only root, circuit breaker
  stacks/              one state file per stack, applied in numeric order
    00-account/        P  budgets + ops-alerts SNS topic
    05-registry/       P  ECR repositories (simulator, consumer)
    10-network/        P  VPC over 3 AZs: public/app/data subnets, S3 endpoint, flow logs, security groups
    15-nat/            E  NAT gateway per AZ + default routes for the app subnets
    18-data/           P  S3: datasets, artefacts, scored archive, endpoint data capture
    19-ml/             P  IAM roles (endpoint, ECS tasks), model registry group
    30-streaming/      E  MSK (3 x m7g.large, KRaft, IAM auth, TLS) + ECS cluster
    35-apps/           E  simulator + consumer services
    40-serving/        E  SageMaker endpoint fs-fraud from the approved model package, data capture
.github/workflows/
  terraform-ci.yml       fmt, validate, tflint, Checkov -> single "ci-passed" check
  python-ci.yml          ruff + pytest -> single "python-ci-passed" check
  images.yml             build, smoke-test and Trivy-scan app images (ARM64); push to ECR on main
  terraform-deploy.yml   plan on PR (comment), approval-gated apply on main, one-click rebuild
  terraform-destroy.yml  nightly teardown of ephemeral stacks + SageMaker sweep + verify
docker/                app container images (simulator, consumer)
docs/security.md       image scanning policy, accepted risk, hardening backlog
scripts/
  setup-github.sh      repo variables, secret, environments, branch protection
  data/make_dataset.py generate a dataset version -> data/ and s3://fs-datasets-.../
  train/train_baseline.py  train + evaluate the baseline model -> models/<run-id>/
  train/package_model.py   model card + SageMaker model.tar.gz -> s3://fs-artefacts-.../
  serve/register_model.py  register a published run in the model registry
  serve/check_endpoint.py  endpoint vs local scores (training-serving parity)
  ops/check_scoring.py     scored archive: lost or duplicated transactions, latency
src/fraudstream/       the Python package (pip install -e ".[dev,train,stream]")
  generator/           synthetic cardholders, shops, transactions and fraud, from one seed
  features/            FeatureBuilder: the one place features are calculated
  simulator/           streams synthetic transactions into Kafka (python -m fraudstream.simulator)
  consumer/            scores the stream with the deployed model (python -m fraudstream.consumer)
  msk.py               MSK connection settings (IAM auth) and topic set-up
  registry.py          model package requests for the SageMaker model registry
  endpoint.py          endpoint request/reply format (CSV) and parity comparison
  storage.py           immutable dataset versions: Parquet + manifest, local and S3
  training/            time split, XGBoost training, evaluation at an alert budget
tests/                 pytest: generator, features, leakage, skew, reproducibility, registry,
                       endpoint format, consumer (scoring, checkpoint order, hand-overs), audit
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
