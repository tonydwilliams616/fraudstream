# -----------------------------------------------------------------------------
# 19-ml/main.tf - shared names, inputs from 18-data, and the model registry.
#
# Model package group: the Model Registry's "catalogue" for this model. Every
# candidate model is registered here as a numbered version with its metrics
# and an approval status (PendingManualApproval -> Approved / Rejected). The
# evaluation gate (Phase 3) registers candidates; only an Approved version is
# deployed. The group is just a record - the model files stay in the
# artefacts bucket, and each version points at its model.tar.gz.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "data" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "18-data/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  name    = var.project_name
  buckets = data.terraform_remote_state.data.outputs.bucket_arns # datasets, artefacts, scored, capture

  # Fixed names the ephemeral stacks will use (see variables.tf for why).
  msk_cluster_name = "${local.name}-msk"
  endpoint_name    = "${local.name}-fraud"
  consumer_group   = "${local.name}-scorer"

  # MSK IAM resource ARNs. The "*" is the cluster's random ID, which changes on
  # every rebuild; the cluster NAME and topic/group names are fixed.
  kafka_arn_prefix = "arn:aws:kafka:${var.aws_region}:${var.aws_account_id}"
  kafka_cluster    = "${local.kafka_arn_prefix}:cluster/${local.msk_cluster_name}/*"
  kafka_topic      = { for k, t in var.kafka_topics : k => "${local.kafka_arn_prefix}:topic/${local.msk_cluster_name}/*/${t}" }
  kafka_group      = "${local.kafka_arn_prefix}:group/${local.msk_cluster_name}/*/${local.consumer_group}"
}

resource "aws_sagemaker_model_package_group" "fraud" {
  model_package_group_name        = "${local.name}-fraud-detection"
  model_package_group_description = "Card-fraud XGBoost models. Candidates are registered by the evaluation gate; only Approved versions are deployed."
}
