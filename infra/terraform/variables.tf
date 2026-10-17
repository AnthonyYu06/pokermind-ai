variable "enabled" {
  type    = bool
  default = false
  validation {
    condition     = !var.enabled || (jsondecode(file("${path.module}/../../configs/cost-policy.json")).cloud_enabled && jsondecode(file("${path.module}/../../configs/cost-policy.json")).per_run_budget_usd > 0)
    error_message = "Cloud is disabled by the central cost policy. A reviewed budget approval is required."
  }
}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "enable_app" {
  type        = bool
  default     = false
  description = "Batch infrastructure can be prepared without provisioning an app VM."
}
variable "owner" {
  type    = string
  default = "local-user"
}
variable "expires_at" {
  type    = string
  default = "UNAPPROVED"
}
variable "account_id" {
  type    = string
  default = "000000000000"
  validation {
    condition     = !var.enabled || can(regex("^[0-9]{12}$", var.account_id)) && var.account_id != "000000000000"
    error_message = "Supply the approved AWS account ID."
  }
}
variable "vpc_id" {
  type    = string
  default = ""
}
variable "subnet_id" {
  type    = string
  default = ""
}
variable "ami_id" {
  type        = string
  default     = ""
  description = "Reviewed Amazon Linux 2023 x86_64 AMI; not automatically resolved at apply."
}
variable "image_digest" {
  type    = string
  default = ""
  validation {
    condition     = !var.enabled || !var.enable_app || can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "Pin a tested ECR image digest before provisioning."
  }
}
variable "instance_type" {
  type    = string
  default = "t3.small"
  validation {
    condition     = !var.enabled || !var.enable_app || contains(jsondecode(file("${path.module}/../../configs/cost-policy.json")).allowed_instance_types, var.instance_type)
    error_message = "Instance type must be in the approved allowlist."
  }
}
variable "run_minutes" {
  type    = number
  default = 60
  validation {
    condition     = !var.enabled || !var.enable_app || (var.run_minutes > 0 && var.run_minutes <= jsondecode(file("${path.module}/../../configs/cost-policy.json")).max_run_minutes)
    error_message = "Runtime must fit the approved maximum."
  }
}
variable "snapshot_sha256" {
  type    = string
  default = ""
  validation {
    condition     = !var.enabled || !var.enable_app || can(regex("^[0-9a-f]{64}$", var.snapshot_sha256))
    error_message = "Pin a reviewed synthetic publication snapshot before starting the app."
  }
}
variable "publication_sha256" {
  type    = string
  default = ""
  validation {
    condition     = !var.enabled || !var.enable_app || can(regex("^[0-9a-f]{64}$", var.publication_sha256))
    error_message = "Pin the reviewed publication manifest SHA256."
  }
}
