# -----------------------------------------------------------------------------
# 40-serving/model.auto.tfvars - THE model in production.
#
# Promoting a model = a PR that changes this one line to another Approved
# version of fs-fraud-detection (scripts/serve/register_model.py prints it).
# The PR's plan shows exactly what changes; merging + approving the deploy
# rolls it out. Rolling back = reverting the commit. The nightly rebuild always
# restores this version.
#
# Version 1: baseline-20261106T120445Z - XGBoost 3.0.5, 117 trees,
# test PR-AUC 0.795, recall 0.72 at ~79 alerts/day. Approved 7 Nov 2026.
# -----------------------------------------------------------------------------
model_package_arn = "arn:aws:sagemaker:us-east-1:831547420318:model-package/fs-fraud-detection/1"
