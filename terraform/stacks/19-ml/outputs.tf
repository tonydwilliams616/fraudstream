# -----------------------------------------------------------------------------
# 19-ml/outputs.tf - roles and names read by the ephemeral stacks:
#   30-streaming  msk_cluster_name, kafka_topics
#   35-apps       task roles, task execution role, topics, dataset version,
#                 consumer group, endpoint name
#   40-serving    SageMaker execution role, endpoint name, model package group
#   pipeline      training pipeline role, name and S3 prefix (pipelines/, GitHub Actions)
# Publishing the names here means the cluster, topics and endpoint are always
# called exactly what the IAM policies above allow.
# -----------------------------------------------------------------------------
output "model_package_group_name" {
  description = "SageMaker Model Registry group for the fraud model."
  value       = aws_sagemaker_model_package_group.fraud.model_package_group_name
}

output "sagemaker_execution_role_arn" {
  description = "Role the SageMaker endpoint runs as."
  value       = aws_iam_role.sagemaker_execution.arn
}

output "ecs_task_execution_role_arn" {
  description = "Role ECS uses to pull images and write logs."
  value       = aws_iam_role.ecs_task_execution.arn
}

output "task_role_arns" {
  description = "Task roles by app: simulator, consumer."
  value = {
    simulator = aws_iam_role.simulator.arn
    consumer  = aws_iam_role.consumer.arn
  }
}

output "msk_cluster_name" {
  description = "Name 30-streaming must give the MSK cluster (the Kafka IAM policies are scoped to it)."
  value       = local.msk_cluster_name
}

output "kafka_topics" {
  description = "Kafka topic names."
  value       = var.kafka_topics
}

output "consumer_group" {
  description = "Kafka consumer group the consumer must use."
  value       = local.consumer_group
}

output "endpoint_name" {
  description = "Name 40-serving must give the SageMaker endpoint (the consumer may invoke only this one)."
  value       = local.endpoint_name
}

output "dataset_version" {
  description = "Dataset version the apps read reference tables from (the task roles may read only this one)."
  value       = var.dataset_version
}

output "pipeline_role_arn" {
  description = "Role the training pipeline and its jobs run as."
  value       = aws_iam_role.sagemaker_pipeline.arn
}

output "pipeline_name" {
  description = "Name of the SageMaker training pipeline (created by the pipeline workflow)."
  value       = local.pipeline_name
}

output "pipeline_s3_prefix" {
  description = "Where pipeline runs and code live in the artefacts bucket."
  value       = local.pipeline_prefix
}
