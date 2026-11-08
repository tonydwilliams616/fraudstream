# -----------------------------------------------------------------------------
# 35-apps/outputs.tf - what to look at when checking the apps are running.
# -----------------------------------------------------------------------------
output "simulator_service_name" {
  description = "ECS service running the simulator."
  value       = module.simulator.service_name
}

output "simulator_image" {
  description = "Exact image (pinned by digest) the simulator task runs."
  value       = module.simulator.image
}

output "simulator_task_definition_arn" {
  description = "Task definition revision the simulator service runs."
  value       = module.simulator.task_definition_arn
}

output "simulator_log_group" {
  description = "CloudWatch log group with the simulator's output."
  value       = module.simulator.log_group
}

output "consumer_service_name" {
  description = "ECS service running the consumer."
  value       = module.consumer.service_name
}

output "consumer_image" {
  description = "Exact image (pinned by digest) the consumer task runs."
  value       = module.consumer.image
}

output "consumer_task_definition_arn" {
  description = "Task definition revision the consumer service runs."
  value       = module.consumer.task_definition_arn
}

output "consumer_log_group" {
  description = "CloudWatch log group with the consumer's output."
  value       = module.consumer.log_group
}
