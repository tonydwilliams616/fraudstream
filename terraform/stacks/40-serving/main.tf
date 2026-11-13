# -----------------------------------------------------------------------------
# 40-serving/main.tf - the fraud model behind a SageMaker real-time endpoint.
#
# Three resources, each built from the one before:
#   model            - built FROM the approved model package (not a loose S3
#                      file), so the container image and weights are exactly
#                      what was registered and approved
#   endpoint config  - what to run it on (2 x ml.m5.large) and data capture:
#                      every request and its score copied to the capture bucket,
#                      which Model Monitor compares with the training data to
#                      detect drift (Phase 6)
#   endpoint         - fs-fraud, the HTTPS API the consumer calls (19-ml lets
#                      the consumer invoke this name and nothing else)
# Why it's built this way:
#   - Which model runs is decided in model.auto.tfvars by a reviewed commit
#     (GitOps): the plan shows the change, rollback is a revert, and the
#     nightly rebuild restores the same version.
#   - A new model creates a NEW model and endpoint config before the old ones
#     are deleted (create_before_destroy), and SageMaker switches the live
#     endpoint over with a blue/green CANARY deployment:
#       1. a complete new fleet (green) starts next to the old one (blue)
#       2. 1 of its 2 instances' worth of traffic (50%) goes to green for 5 min
#          while the alarms in alarms.tf watch it (canary bake)
#       3. no alarm -> all traffic moves to green; blue is kept 2 more minutes
#          as a fast way back, then terminated
#       any alarm during this -> all traffic returns to blue automatically
#     Two instances because a canary must be at most half the new fleet; they
#     also run in different Availability Zones, so one AZ failing doesn't take
#     scoring down.
#   - Network isolation: the model container has no network access at all. It
#     only needs its weights, which SageMaker loads for it.
# Approval: Terraform has no data source for a model package's status, so the
#   deploy workflow checks it (src/fraudstream/promotion.py): on the PR's plan,
#   and again right before this stack is applied.
# Cost: 2 x ml.m5.large ~$0.23/hour while up; during a deployment both fleets
#   run (~$0.46/hour for ~15 minutes). Create time: ~6-10 minutes; a
#   blue/green model update takes ~15 minutes.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "data" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "18-data/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "ml" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "19-ml/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  name          = var.project_name
  ml            = data.terraform_remote_state.ml.outputs
  model_version = regex("[0-9]+$", var.model_package_arn) # e.g. "1"
  # SageMaker appends <endpoint>/<variant>/<yyyy>/<mm>/<dd>/<hh>/ to this prefix,
  # so files land in datacapture/fs-fraud/AllTraffic/... - Model Monitor reads them there.
  capture_uri = "s3://${data.terraform_remote_state.data.outputs.bucket_names["capture"]}/datacapture"
}

resource "aws_sagemaker_model" "fraud" {
  name                     = "${local.name}-fraud-v${local.model_version}"
  execution_role_arn       = local.ml.sagemaker_execution_role_arn
  enable_network_isolation = true

  primary_container {
    model_package_name = var.model_package_arn
  }

  tags = { ModelPackageVersion = local.model_version }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_sagemaker_endpoint_configuration" "fraud" {
  #checkov:skip=CKV_AWS_98:Endpoint storage is encrypted with the AWS-managed key; a customer-managed KMS key adds key management with no benefit for synthetic data
  name_prefix = "${local.name}-fraud-v${local.model_version}-"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.fraud.name
    instance_type          = var.instance_type
    initial_instance_count = var.instance_count
    initial_variant_weight = 1
  }

  data_capture_config {
    enable_capture              = true
    initial_sampling_percentage = var.capture_sampling_percentage
    destination_s3_uri          = local.capture_uri

    capture_options {
      capture_mode = "Input"
    }

    capture_options {
      capture_mode = "Output"
    }

    capture_content_type_header {
      csv_content_types = ["text/csv"]
    }
  }

  tags = { ModelPackageVersion = local.model_version }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_sagemaker_endpoint" "fraud" {
  name                 = local.ml.endpoint_name # fs-fraud
  endpoint_config_name = aws_sagemaker_endpoint_configuration.fraud.name

  # Used whenever endpoint_config_name changes (a new model, or new settings).
  deployment_config {
    blue_green_update_policy {
      traffic_routing_configuration {
        type                     = "CANARY"
        wait_interval_in_seconds = 300 # canary bake: 5 minutes on 50% of traffic

        canary_size {
          type  = "INSTANCE_COUNT"
          value = 1 # 1 of 2 instances - the most a canary may be is half the fleet
        }
      }

      termination_wait_in_seconds          = 120  # keep blue 2 minutes after the switch
      maximum_execution_timeout_in_seconds = 1800 # give up (and roll back) after 30 minutes
    }

    auto_rollback_configuration {
      alarms {
        alarm_name = aws_cloudwatch_metric_alarm.errors_5xx.alarm_name
      }
      alarms {
        alarm_name = aws_cloudwatch_metric_alarm.errors_4xx.alarm_name
      }
      alarms {
        alarm_name = aws_cloudwatch_metric_alarm.latency_p99.alarm_name
      }
    }
  }

  tags = { ModelPackageVersion = local.model_version }
}
