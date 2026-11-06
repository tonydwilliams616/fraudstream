# -----------------------------------------------------------------------------
# 10-network/flow-logs.tf - a record of every network connection in the VPC.
#
# What: VPC flow logs (who connected to whom, on which port, accepted or
#       rejected) sent to CloudWatch Logs, kept for 14 days.
# Why:  the first thing you need when "the consumer can't reach Kafka" - a
#       REJECT line shows exactly which security group or route is in the way.
#       Also a baseline security control: there's a record of all traffic.
# Cost: charged per GB of log data. An idle VPC produces almost none; a session
#       at ~10 transactions/second produces a few MB.
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "flow_logs" {
  #checkov:skip=CKV_AWS_158:CloudWatch Logs encrypts at rest by default; a customer-managed KMS key adds cost for no benefit on synthetic data
  #checkov:skip=CKV_AWS_338:14 days is enough to troubleshoot a session; a year of flow logs isn't needed for a portfolio account
  name              = "/${local.name}/vpc-flow-logs"
  retention_in_days = var.flow_log_retention_days
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }

    # Only flow logs from THIS account can use the role (confused-deputy guard).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.aws_account_id]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name               = "${local.name}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

data "aws_iam_policy_document" "flow_logs_write" {
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.flow_logs.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  name   = "write-flow-logs"
  role   = aws_iam_role.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs_write.json
}

resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn             = aws_iam_role.flow_logs.arn
  max_aggregation_interval = 60 # seconds - faster feedback while debugging

  tags = { Name = "${local.name}-vpc" }
}
