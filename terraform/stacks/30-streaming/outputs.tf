# -----------------------------------------------------------------------------
# 30-streaming/outputs.tf - what the apps (35-apps) and monitoring
# (50-observability) need to connect to Kafka and run tasks.
# -----------------------------------------------------------------------------
output "msk_cluster_arn" {
  description = "MSK cluster ARN (its random ID changes on every rebuild)."
  value       = aws_msk_cluster.this.arn
}

output "kafka_version" {
  description = "Kafka version the cluster runs."
  value       = aws_msk_cluster.this.kafka_version
}

output "bootstrap_brokers_iam" {
  description = "Comma-separated broker addresses for IAM-authenticated clients (port 9098). Clients connect to any one and discover the rest."
  value       = aws_msk_cluster.this.bootstrap_brokers_sasl_iam
}

output "ecs_cluster_name" {
  description = "ECS cluster for the Fargate tasks."
  value       = aws_ecs_cluster.this.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}
