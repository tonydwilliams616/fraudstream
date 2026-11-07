# -----------------------------------------------------------------------------
# 40-serving/outputs.tf - what the consumer and the checks need.
# -----------------------------------------------------------------------------
output "endpoint_name" {
  description = "Endpoint the consumer invokes (InvokeEndpoint, text/csv)."
  value       = aws_sagemaker_endpoint.fraud.name
}

output "model_package_arn" {
  description = "Model package version being served."
  value       = var.model_package_arn
}

output "model_name" {
  description = "SageMaker model built from that package."
  value       = aws_sagemaker_model.fraud.name
}

output "data_capture_uri" {
  description = "Where captured requests and scores land (Model Monitor reads this in Phase 6)."
  value       = local.capture_uri
}
