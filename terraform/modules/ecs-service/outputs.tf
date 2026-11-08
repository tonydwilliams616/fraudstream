# -----------------------------------------------------------------------------
# modules/ecs-service/outputs.tf - what to look at when checking the app.
# -----------------------------------------------------------------------------
output "service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.this.name
}

output "image" {
  description = "Exact image (pinned by digest) the task runs."
  value       = local.image
}

output "task_definition_arn" {
  description = "Task definition revision the service runs."
  value       = aws_ecs_task_definition.this.arn
}

output "log_group" {
  description = "CloudWatch log group with the app's output."
  value       = aws_cloudwatch_log_group.this.name
}
