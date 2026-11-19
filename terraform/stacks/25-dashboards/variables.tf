# -----------------------------------------------------------------------------
# 25-dashboards/variables.tf - inputs for Grafana's data sources and dashboards.
# -----------------------------------------------------------------------------
variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "The fraudstream-dev account ID. The provider refuses to run against any other account."
  type        = string
  default     = "831547420318"
}

# How each data source signs its AWS requests. Both should be Managed Grafana's
# "workspace IAM role" setting; the values are copied from data sources created
# once in the UI (Apps > AWS Data Sources) and read back from /api/datasources.
variable "prometheus_auth_type" {
  description = "sigV4AuthType for the Managed Prometheus data source (workspace IAM role)."
  type        = string
  default     = "ec2_iam_role"
}

variable "cloudwatch_auth_type" {
  description = "authType for the CloudWatch data source (workspace IAM role)."
  type        = string
  default     = "ec2_iam_role"
}
