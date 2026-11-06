# -----------------------------------------------------------------------------
# 30-streaming/main.tf - the Kafka cluster (Amazon MSK) and the ECS cluster.
#
# MSK: three brokers, one per AZ, in the data subnets (no internet route).
#   Durability (decision D4): every partition has 3 copies (one per AZ); a
#   write counts only once 2 copies have it (min.insync.replicas=2, producers
#   use acks=all). One AZ can fail with no data loss and writes carry on.
#   Security: clients authenticate with IAM (port 9098, TLS) - there's no
#   unauthenticated or plaintext listener - and each app's IAM role allows only
#   its own topics (19-ml). Data is encrypted in transit, including between
#   brokers, and at rest.
#   Topics are never created by accident: auto.create.topics.enable=false, so
#   a typo in a topic name is an error, not a new empty topic.
#   Monitoring: Prometheus exporters on every broker (scraped in Phase 5),
#   per-broker CloudWatch metrics, and broker logs to CloudWatch Logs.
# ECS: the cluster the simulator, consumer and monitoring tasks run in
#   (35-apps, 50-observability). The cluster itself costs nothing; tasks do.
# Cost: 3 x kafka.m7g.large ~$0.61/hour + storage (~$0.02/hour for 150 GB).
# Create time: typically 25-40 minutes; destroy 10-20 minutes.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "10-network/terraform.tfstate"
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

# Fails the plan if MSK doesn't offer this exact version, rather than the
# apply failing 10 minutes in.
data "aws_msk_kafka_version" "selected" {
  preferred_versions = [var.kafka_version]
}

locals {
  name         = var.project_name
  net          = data.terraform_remote_state.network.outputs
  cluster_name = data.terraform_remote_state.ml.outputs.msk_cluster_name # fs-msk
}

resource "aws_cloudwatch_log_group" "broker" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key adds cost for no benefit on synthetic data
  #checkov:skip=CKV_AWS_338:Broker logs are for troubleshooting a session; 7 days is enough
  name              = "/${local.name}/msk/broker"
  retention_in_days = var.log_retention_days
}

resource "aws_msk_configuration" "this" {
  name           = "${local.cluster_name}-config"
  description    = "fraudstream broker settings: 3 copies, 2 must be in sync, no automatic topic creation"
  kafka_versions = [data.aws_msk_kafka_version.selected.version]

  server_properties = <<-PROPERTIES
    auto.create.topics.enable=false
    default.replication.factor=3
    min.insync.replicas=2
    num.partitions=6
    unclean.leader.election.enable=false
    log.retention.hours=${var.retention_hours}
  PROPERTIES
}

resource "aws_msk_cluster" "this" {
  #checkov:skip=CKV_AWS_81:Encrypted at rest with the AWS-managed MSK key; a customer-managed key adds key lifecycle management with no benefit for synthetic data
  cluster_name           = local.cluster_name
  kafka_version          = data.aws_msk_kafka_version.selected.version
  number_of_broker_nodes = length(local.net.data_subnet_ids) # one per AZ
  enhanced_monitoring    = "PER_BROKER"

  broker_node_group_info {
    instance_type   = var.broker_instance_type
    client_subnets  = local.net.data_subnet_ids
    security_groups = [local.net.security_group_ids["msk"]]

    storage_info {
      ebs_storage_info {
        volume_size = var.broker_volume_gb
      }
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.this.arn
    revision = aws_msk_configuration.this.latest_revision
  }

  client_authentication {
    unauthenticated = false

    sasl {
      iam = true
    }
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  open_monitoring {
    prometheus {
      jmx_exporter {
        enabled_in_broker = true
      }
      node_exporter {
        enabled_in_broker = true
      }
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.broker.name
      }
    }
  }

  timeouts {
    create = "90m"
    update = "120m"
    delete = "60m"
  }
}

resource "aws_ecs_cluster" "this" {
  name = "${local.name}-apps"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}
