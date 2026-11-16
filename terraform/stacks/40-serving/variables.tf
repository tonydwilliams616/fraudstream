# -----------------------------------------------------------------------------
# 40-serving/variables.tf - which model to serve, and on what.
# The model is chosen in model.auto.tfvars (a reviewed, committed change);
# the endpoint name and roles come from 19-ml.
# -----------------------------------------------------------------------------
variable "project_name" {
  description = "Short prefix used for naming resources (fs = fraudstream)."
  type        = string
  default     = "fs"
}

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

variable "model_package_arn" {
  description = "Approved model package (version) in the fs-fraud-detection group to serve. Set in model.auto.tfvars; changing it is a model promotion."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:sagemaker:[a-z0-9-]+:[0-9]{12}:model-package/fs-fraud-detection/[0-9]+$", var.model_package_arn))
    error_message = "model_package_arn must be a VERSION of the fs-fraud-detection group, e.g. arn:aws:sagemaker:us-east-1:<account>:model-package/fs-fraud-detection/1."
  }
}

variable "previous_model_package_arn" {
  description = "The last-known-good version, kept deployed (model + endpoint config, no instances) so a canary rollback has somewhere to land. Set by `promotion promote`; empty when there is none."
  type        = string
  default     = ""

  validation {
    condition     = var.previous_model_package_arn == "" || can(regex("^arn:aws:sagemaker:[a-z0-9-]+:[0-9]{12}:model-package/fs-fraud-detection/[0-9]+$", var.previous_model_package_arn))
    error_message = "previous_model_package_arn must be empty or a VERSION of the fs-fraud-detection group."
  }
}

variable "instance_type" {
  description = "Endpoint instance type. ml.m5.large (2 vCPU, 8 GiB) is ample for XGBoost at ~10 transactions/second. ~$0.115/hour."
  type        = string
  default     = "ml.m5.large"
}

variable "instance_count" {
  description = "Instances behind the endpoint. At least 2: a canary deployment sends 1 instance's worth of traffic to the new model, which must be at most half the fleet; 2 instances also span two Availability Zones."
  type        = number
  default     = 2

  validation {
    condition     = var.instance_count >= 2
    error_message = "instance_count must be at least 2 for canary deployments (a canary may be at most half the new fleet)."
  }
}

variable "max_instance_count" {
  description = "Most instances auto scaling may run. Each extra ml.m5.large costs ~$0.115/hour while scaled out."
  type        = number
  default     = 4

  validation {
    condition     = var.max_instance_count >= var.instance_count
    error_message = "max_instance_count must be at least instance_count."
  }
}

variable "invocations_per_instance_target" {
  description = "Target tracking value: endpoint calls per instance per minute. Normal traffic is ~6 (each call scores up to 50 transactions); 100 leaves headroom while letting the load test prove scale-out."
  type        = number
  default     = 100
}

variable "capture_sampling_percentage" {
  description = "Share of requests (and their scores) copied to the capture bucket for drift monitoring. 100 = every request."
  type        = number
  default     = 100

  validation {
    condition     = var.capture_sampling_percentage >= 1 && var.capture_sampling_percentage <= 100
    error_message = "capture_sampling_percentage must be between 1 and 100."
  }
}
