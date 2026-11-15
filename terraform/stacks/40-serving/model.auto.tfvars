# -----------------------------------------------------------------------------
# 40-serving/model.auto.tfvars - THE model in production.
#
# Promoting a model = a PR that changes this file, made with
#   python -m fraudstream.promotion promote --version <N>
# which refuses any version that isn't Approved in the registry. The PR's
# plan shows exactly what changes; merging + approving the deploy rolls it
# out (blue/green). Rolling back = reverting the commit. The nightly rebuild
# always restores this version.
#
# Version 2: ijq5pvnomvfz: test PR-AUC 0.8103, recall 0.7245 at 77.4 alerts/day
# Run ijq5pvnomvfz, commit 89815cea1ddb, dataset transactions/v2, XGBoost 3.0.5.
# Status Approved (14 Nov 2026): Pipeline run ijq5pvnomvfz: test PR-AUC 0.810 vs v1 0.795 on the same days, recall 0.725 vs 0.715; gate passed. Approved for canary rollout.
# -----------------------------------------------------------------------------
model_package_arn = "arn:aws:sagemaker:us-east-1:831547420318:model-package/fs-fraud-detection/2"
