# fraudstream

Real-time card-fraud scoring that keeps itself healthy. Transactions stream through
Kafka (Amazon MSK), are scored by an XGBoost model on a SageMaker real-time endpoint,
and data drift triggers a gated retrain and a blue/green promotion. Everything is
Terraform, delivered by GitHub Actions, and torn down nightly to control cost.

The focus is the **ML lifecycle** – data versioning, evaluation gates, safe deployment,
drift detection, retraining, DR – not model accuracy.

## Status

Phase 1 (foundations, data and baseline model) – complete. Phase 2 (streaming and serving) –
complete. Phase 3 (training pipeline and evaluation gate) – complete. Phase 4 (safe deployment
and scaling) – complete. Next: Phase 5 (observability and SLOs).

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

### Phase 3 – training pipeline and evaluation gate

```
merge to main (model code) -> Container images -> Training pipeline workflow -> SageMaker Pipeline fs-fraud-training

  Prepare (fs/pipeline) -> Train (XGBoost 3.0-5, script mode) -> Evaluate (challenger vs champion, same days)
     -> GatePassed?  yes -> Register (PendingManualApproval)
                     no  -> GateRejected (the run fails, with the reason)
```

- **Pipeline:** the definition is built as plain data in `src/fraudstream/pipeline/definition.py`
  (no SageMaker SDK), unit-tested in CI, and upserted and started by
  `training-pipeline.yml` – after the images build for a merge that changed model code, or by
  hand with hyperparameter overrides. Nothing runs between runs; each run is four short jobs.
- **Training-serving parity by construction:** training runs in SageMaker's built-in XGBoost
  3.0-5 image, the same image the endpoint serves with. Prepare, evaluate and register run in
  our own `fs/pipeline` image, built from the same package.
- **Gate (champion vs challenger):** the champion is the newest *Approved* package, re-scored
  by the same code on the same days. The challenger needs PR-AUC ≥ 0.6, PR-AUC ≥ champion − 0.02
  and recall ≥ champion − 0.05. The rules are code, not run-time options: changing them needs a
  reviewed PR.
- **Approval stays separate:** a passing model is registered as `PendingManualApproval`. The
  pipeline's role cannot approve packages, and the endpoint still serves the version pinned in
  `model.auto.tfvars` – training, approval and deployment are three separate decisions.
- **Lineage:** one commit names everything in a run – the code checked out, the uploaded code
  bundle, the image tag and the package's `GitCommit` – and the package records the dataset
  version and fingerprint, run ID and full metrics.
- **Least privilege:** GitHub uses `fs-github-pipeline` (main only; can manage this one pipeline,
  pass its role to SageMaker and upload code). The jobs run as `fs-sagemaker-pipeline` (read
  datasets and models, write only under `models/fraud-pipeline/`, register but not approve).

**Measured (November 2026):**

| Run | Challenger (test days) | Champion v1, same days | Gate | Result |
|---|---|---|---|---|
| Normal settings | PR-AUC 0.810, recall 0.725, 77 alerts/day | PR-AUC 0.795, recall 0.715 | passed (needs ≥ 0.775 and ≥ 0.665) | v2 registered, `PendingManualApproval` |
| Deliberately weak (1 tree, depth 1) | PR-AUC 0.022, recall 0.955, 3,461 alerts/day | PR-AUC 0.795, recall 0.715 | failed: PR-AUC floor and vs champion | rejected, nothing registered |

A run takes ~14 minutes and costs ~$0.10. The weak model's *recall* was higher than the
champion's – it alerts on almost everything – which is why the gate leads with PR-AUC.

**Check it yourself:** `gh workflow run training-pipeline.yml --ref main -f n_estimators=1 -f max_depth=1 -f expect=rejected`
(the job passes only if the gate rejects the model).

### Phase 4 – safe deployment and scaling

```
promote --version N  ->  PR (approval check + plan guard)  ->  merge + approve deploy
   ->  approval checked again  ->  blue/green CANARY on fs-fraud  ->  verify live model == git
          green fleet takes 50% for 5 min while 3 alarms watch
          alarm  -> SageMaker returns all traffic to the last-known-good model by itself
          no alarm -> green takes 100%, blue kept 2 more minutes, then removed
```

- **Promotion is a reviewed commit.** `python -m fraudstream.promotion promote --version N`
  refuses any package that isn't Approved, then rewrites `model.auto.tfvars` with the new
  version, a comment recording its metrics, lineage and approval note, and the version it
  replaces as `previous_model_package_arn`. Approval is checked three times: on the PR's
  plan, right before 40-serving is applied, and by the consumer at run time.
- **Canary with automatic rollback.** Two instances (a canary may be at most half the new
  fleet; two also span two AZs). Alarms on 4xx errors (a model that fails on its input is
  reported as a 4xx `ModelError`), 5xx errors and p99 model latency above 500 ms. They catch
  a model that is broken or slow; a model that is quietly *wrong* is the evaluation gate's
  job before deployment and drift monitoring's after it.
- **Last-known-good stays deployed.** The served version and the previous one each keep a
  model and an endpoint config (`for_each`), so a rollback always has a config to return to.
  The oldest is deleted by the next promotion.
- **Verify the outcome, not the exit code.** After every 40-serving apply,
  `promotion verify` follows the live endpoint to the model package it really serves and
  fails the deploy unless it matches git - naming a rollback when that is what happened.
- **Plan guard.** Every plan is checked (`src/fraudstream/deploy_guard.py`): a plan that would
  replace or destroy the endpoint or the MSK cluster is refused (red on the PR, a hard stop on
  apply) unless a hand-started run sets `allow_replace`.
- **Auto scaling.** Target tracking keeps endpoint calls per instance near 100 a minute,
  between 2 and 4 instances; it scales out fast and in slowly. Normal traffic is ~6 calls per
  instance per minute (each call scores up to 50 transactions).

**Measured (November 2026):**

| Check | Result |
|---|---|
| v2 promoted (PR-AUC 0.810 vs v1 0.795 on the same days) | serving v2; `verify` confirms the live package matches git |
| Deliberately broken model v4 (13 features instead of 14), approved as a labelled test | canary started 17:13:18, `fs-fraud-errors-4xx` in ALARM 17:16:37, all traffic back on v2 by 17:17:09 - no human involved; deploy red: *"rollout of v4 was ROLLED BACK"* |
| Transactions during the broken canary | 17,874 scored in 30 minutes, 0 lost, 0 scored twice; the consumer stopped once when 4 retries in a row hit the broken fleet and resumed from its last commit (worst delay 218 s) |
| Load test, ~575 calls/min (real test-day rows) | scaled 2 -> 3 within ~2 min, then to the maximum of 4; 0 errors; p99 latency 421 -> 280 -> 216 ms as instances arrived (p50 ~105 ms throughout); scaled back in one step at a time after the load stopped |
| Training reproducibility | a second pipeline run on the same data, code and seed produced an identical model (PR-AUC 0.8103, recall 0.7245) |

**Check it yourself:** `python scripts/serve/register_broken_model.py`, approve it as a test,
`promote` it and watch it roll back; `python scripts/ops/load_test.py --per-minute 600 --minutes 15`
(use `caffeinate -i` on a Mac so it doesn't sleep mid-test).

#### Post-incident reviews

**13 Nov - endpoint replaced by a reviewed plan (~3.5 minutes without scoring).** Adding the
rollback block (`auto_rollback_configuration`) to the endpoint is a *forces replacement* change
in the AWS provider: Terraform deleted and recreated the endpoint. The PR's plan said so; review
missed it. Nothing was lost - transactions waited in Kafka (17,800, 0 lost, worst delay ~4.5 min).
*Fix:* the plan guard now refuses any plan that replaces the endpoint or MSK.

**14 Nov - v2 promotion, ~81 minutes without scoring, 0 transactions lost.**

| Time (UTC) | What happened |
|---|---|
| 12:39 | Deploy starts the v1 -> v2 blue/green update |
| 12:42 | SageMaker abandons it before the canary: the endpoint's role lacks `cloudwatch:DescribeAlarms` (SageMaker checks the rollback alarms as the endpoint's role). Customers stay on v1 |
| 12:43 | Terraform reports "Modifications complete" (it only waited for `InService`), the deploy goes green, then Terraform **deletes the v1 config and model the endpoint is still running on** |
| ~12:45 | The consumer's model lookup fails on the deleted config; it crash-loops (39 restarts). Scoring stops; transactions wait in Kafka. No alert fires |
| 13:30 | Deploy deadlock: the fix's merge rebuilt the app images, so 35-apps waited for a consumer that couldn't start until 40-serving - which runs after 35-apps - was fixed |
| 13:55 | The endpoint can't be updated at all: a blue/green update needs the current config, which no longer exists |
| 14:00 | Recovery: endpoint deleted and recreated on v2 (a create, which the guard allows); consumer drains the backlog |

Impact: ~81 minutes of delayed fraud checks; 64,699 transactions, 0 lost, 0 scored twice.

| Root cause | Fix |
|---|---|
| Endpoint role couldn't read the rollback alarms | `DescribeAlarms` added - on `"*"`: scoped to the alarm ARNs it is denied, because SageMaker's check is evaluated against `"*"` (found with the IAM policy simulator on 15 Nov) |
| Terraform reported an abandoned update as success, then deleted in-use objects | `promotion verify` after every apply; last-known-good model and config kept deployed |
| Consumer crash-loops when its model lookup fails | Backlog: keep the last known model and retry instead of exiting |
| Nobody was alerted for 80 minutes | Phase 5: alarms on consumer running count and consumer lag |
| A broken dependency blocked the deploy of its own fix | Single-stack deploy as the break-glass path |
| Stale state lock after a cancelled run | `terraform force-unlock`, only once the lock holder is confirmed dead |

Also seen: a 90-minute Terraform timeout while MSK was still creating (AWS took ~1h45m) tainted a
healthy cluster; it was checked in AWS and untainted rather than replaced.

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
    05-registry/       P  ECR repositories (simulator, consumer, pipeline)
    10-network/        P  VPC over 3 AZs: public/app/data subnets, S3 endpoint, flow logs, security groups
    15-nat/            E  NAT gateway per AZ + default routes for the app subnets
    18-data/           P  S3: datasets, artefacts, scored archive, endpoint data capture
    19-ml/             P  IAM roles (endpoint, ECS tasks, training pipeline), model registry group
    30-streaming/      E  MSK (3 x m7g.large, KRaft, IAM auth, TLS) + ECS cluster
    35-apps/           E  simulator + consumer services
    40-serving/        E  endpoint fs-fraud: current + last-known-good model, canary deployment,
                          rollback alarms, auto scaling (2-4), data capture
.github/workflows/
  terraform-ci.yml       fmt, validate, tflint, Checkov -> single "ci-passed" check
  python-ci.yml          ruff + pytest -> single "python-ci-passed" check
  images.yml             build, smoke-test and Trivy-scan images (apps ARM64, pipeline x86); push to ECR on main
  training-pipeline.yml  upsert and run the SageMaker training pipeline; by hand with overrides
  terraform-deploy.yml   plan on PR (comment), approval-gated apply on main, one-click rebuild
  terraform-destroy.yml  nightly teardown of ephemeral stacks + SageMaker sweep + verify
docker/                container images (simulator, consumer, pipeline steps)
docs/security.md       image scanning policy, accepted risk, hardening backlog
scripts/
  setup-github.sh      repo variables, secret, environments, branch protection
  data/make_dataset.py generate a dataset version -> data/ and s3://fs-datasets-.../
  train/train_baseline.py  train + evaluate the baseline model -> models/<run-id>/
  train/package_model.py   model card + SageMaker model.tar.gz -> s3://fs-artefacts-.../
  serve/register_model.py  register a published run in the model registry
  serve/check_endpoint.py  endpoint vs local scores (training-serving parity)
  serve/register_broken_model.py  a deliberately broken model for the rollback test
  ops/check_scoring.py     scored archive: lost or duplicated transactions, latency
  ops/load_test.py         real test-day traffic at a fixed rate, to prove auto scaling
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
  pipeline/            pipeline steps, gate, definition and launcher (python -m fraudstream.pipeline)
  promotion.py         promote / check / verify a model version (python -m fraudstream.promotion)
  deploy_guard.py      refuses plans that would replace the endpoint or MSK
tests/                 pytest: generator, features, leakage, skew, reproducibility, registry,
                       endpoint format, consumer (scoring, checkpoint order, hand-overs), audit,
                       pipeline definition and gate, promotion, plan guard
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
