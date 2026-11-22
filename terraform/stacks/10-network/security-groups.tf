# -----------------------------------------------------------------------------
# 10-network/security-groups.tf - who may talk to whom, defined in one place.
#
# Why here and not in each stack: the groups reference each other (apps may
# reach MSK; MSK accepts apps). Defining them together avoids stacks having to
# edit each other's groups, and they cost nothing, so they can stay up. The
# stacks that run things (30-streaming, 35-apps) attach them. Metrics don't
# need a group: the collector is a sidecar inside the consumer's task and
# reaches Managed Prometheus over HTTPS through the apps group.
#
# Ports:
#   9098         Kafka over TLS with IAM authentication (the only client port)
#   443          AWS APIs: SageMaker runtime, ECR, CloudWatch Logs, STS, S3
# Egress is restricted too: no group gets "everything, everywhere".
# -----------------------------------------------------------------------------
locals {
  kafka_iam_port = 9098
}

resource "aws_security_group" "apps" {
  #checkov:skip=CKV2_AWS_5:Attached by the ECS services in 35-apps (another stack), which Checkov can't see from here
  name        = "${local.name}-apps"
  description = "Fargate tasks - simulator and consumer"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-apps" }
}

resource "aws_security_group" "msk" {
  #checkov:skip=CKV2_AWS_5:Attached by the MSK cluster in 30-streaming (another stack), which Checkov can't see from here
  name        = "${local.name}-msk"
  description = "MSK brokers - accept Kafka clients and Prometheus scrapes only"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${local.name}-msk" }
}


# --- apps ---------------------------------------------------------------------
resource "aws_vpc_security_group_egress_rule" "apps_to_msk" {
  security_group_id            = aws_security_group.apps.id
  description                  = "Kafka (TLS + IAM) to the brokers"
  ip_protocol                  = "tcp"
  from_port                    = local.kafka_iam_port
  to_port                      = local.kafka_iam_port
  referenced_security_group_id = aws_security_group.msk.id
}

resource "aws_vpc_security_group_egress_rule" "apps_https" {
  security_group_id = aws_security_group.apps.id
  description       = "HTTPS to AWS APIs (SageMaker runtime, ECR, logs, STS, S3)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# --- msk (no egress rules: brokers never start connections) ---------------------
resource "aws_vpc_security_group_ingress_rule" "msk_from_apps" {
  security_group_id            = aws_security_group.msk.id
  description                  = "Kafka (TLS + IAM) from the app tasks"
  ip_protocol                  = "tcp"
  from_port                    = local.kafka_iam_port
  to_port                      = local.kafka_iam_port
  referenced_security_group_id = aws_security_group.apps.id
}
