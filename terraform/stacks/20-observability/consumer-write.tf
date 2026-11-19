# -----------------------------------------------------------------------------
# 20-observability/consumer-write.tf - lets the consumer send its metrics here.
#
# What: an extra policy on the consumer's task role (created in 19-ml) that
#       allows remote-writing samples to THIS Prometheus workspace and nothing
#       else. The collector beside the consumer (35-apps) signs its requests
#       with that role.
# Why here, not in 19-ml: the workspace is created in this stack, which is
#       applied after 19-ml, so 19-ml can't refer to it. The permission lives
#       next to the thing it grants access to.
# -----------------------------------------------------------------------------
data "terraform_remote_state" "ml" {
  backend = "s3"

  config = {
    bucket = "fs-tfstate-831547420318"
    key    = "19-ml/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  # arn:aws:iam::<account>:role/fs-consumer -> fs-consumer
  consumer_role_name = element(split("/", data.terraform_remote_state.ml.outputs.task_role_arns["consumer"]), 1)
}

data "aws_iam_policy_document" "consumer_remote_write" {
  statement {
    sid       = "WriteMetrics"
    actions   = ["aps:RemoteWrite"]
    resources = [aws_prometheus_workspace.this.arn]
  }
}

resource "aws_iam_role_policy" "consumer_remote_write" {
  name   = "write-metrics"
  role   = local.consumer_role_name
  policy = data.aws_iam_policy_document.consumer_remote_write.json
}
