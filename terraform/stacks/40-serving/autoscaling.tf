# -----------------------------------------------------------------------------
# 40-serving/autoscaling.tf - more instances when traffic rises, fewer when it falls.
#
# What: Application Auto Scaling target tracking on the endpoint's AllTraffic
#       variant. Like a thermostat: it keeps "invocations per instance per
#       minute" near a target by changing the instance count between
#       min (var.instance_count, 2) and max (var.max_instance_count, 4).
#         - scale out quickly (60 s cooldown): falling behind delays fraud checks
#         - scale in slowly (300 s cooldown, and AWS waits ~15 minutes of low
#           traffic before removing an instance): avoid flapping
# Why the minimum is 2: a canary needs 2 instances, and 2 span two AZs.
# With blue/green deployments: scaling is paused while an update is in progress;
#       the new fleet starts at the endpoint config's initial_instance_count (2)
#       and target tracking resizes it afterwards. Changing the model is allowed
#       with auto scaling attached; changing the INSTANCE TYPE is not (deregister
#       the target first - see AWS's "Update endpoints that use auto scaling").
# Target value: invocations_per_instance_target. Our steady stream is ~6 calls
#       per instance per minute (each call scores up to 50 transactions), so the
#       default leaves lots of headroom; the load test (scripts/ops/load_test.py)
#       pushes past it to prove scale-out.
# Terraform and the instance count: the scaling target changes the variant's
#       DesiredInstanceCount at run time. That is not part of the endpoint config
#       (initial_instance_count stays 2), so it doesn't show up as drift.
# Cost: nothing extra at rest; each extra ml.m5.large ~$0.115/hour while scaled out.
# -----------------------------------------------------------------------------
resource "aws_appautoscaling_target" "endpoint" {
  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.fraud.name}/variant/AllTraffic"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.instance_count
  max_capacity       = var.max_instance_count
}

resource "aws_appautoscaling_policy" "invocations" {
  name               = "${local.name}-fraud-invocations-per-instance"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.endpoint.service_namespace
  resource_id        = aws_appautoscaling_target.endpoint.resource_id
  scalable_dimension = aws_appautoscaling_target.endpoint.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.invocations_per_instance_target
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }
  }
}
