# -----------------------------------------------------------------------------
# 40-serving/model.auto.tfvars - THE model in production.
#
# Promoting a model = a PR that changes this file, made with
#   python -m fraudstream.promotion promote --version <N>
# which refuses any version that isn't Approved in the registry. The PR's
# plan shows exactly what changes; merging + approving the deploy rolls it
# out (blue/green). Rolling back = reverting the commit. The nightly rebuild
# always restores this version.
# previous_model_package_arn is the last-known-good version: it stays
# deployed (model + endpoint config, no instances) so that a canary which
# rolls back has a config to return to. promote moves the served version
# here; the one before that is deleted.
#
# Version 4: DELIBERATELY BROKEN broken-demo-20261115T160723Z: expects 13 features, fails every real request. For the canary rollback test only.
# Run broken-demo-20261115T160723Z, commit ?, dataset ?, XGBoost 3.0.5.
# Status Approved (15 Nov 2026): ROLLBACK TEST ONLY: deliberately broken (13 features). Approved to prove canary auto-rollback; reject after the test.
# -----------------------------------------------------------------------------
model_package_arn          = "arn:aws:sagemaker:us-east-1:831547420318:model-package/fs-fraud-detection/4"
previous_model_package_arn = "arn:aws:sagemaker:us-east-1:831547420318:model-package/fs-fraud-detection/2"
