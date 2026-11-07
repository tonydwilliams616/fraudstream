# -----------------------------------------------------------------------------
# 35-apps/outputs.tf - what to look at when checking the apps are running.
# -----------------------------------------------------------------------------
output "simulator_service_name" {
  description = "ECS service running the simulator."
  value       = aws_ecs_service.simulator.name
}

output "simulator_image" {
  description = "Exact image (pinned by digest) the simulator task runs."
  value       = local.simulator_image
}

output "simulator_task_definition_arn" {
  description = "Task definition revision the service runs."
  value       = aws_ecs_task_definition.simulator.arn
}

output "simulator_log_group" {
  description = "CloudWatch log group with the simulator's output."
  value       = aws_cloudwatch_log_group.simulator.name
}
