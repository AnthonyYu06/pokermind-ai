# Alerting supplements admission controls. Billing updates are delayed and these
# resources do not stop compute or turn the estimate into a hard billing cap.
variable "budget_notification_emails" {
  type      = set(string)
  default   = []
  sensitive = true
  validation {
    condition     = !var.enabled || length(var.budget_notification_emails) > 0
    error_message = "Configure reviewed AWS budget notification recipients before enabling infrastructure."
  }
}
variable "project_cost_tag_active" {
  type    = bool
  default = false
  validation {
    condition     = !var.enabled || var.project_cost_tag_active
    error_message = "Verify activation of the Project cost allocation tag before enabling infrastructure."
  }
}
variable "app_alarm_topic_arn" {
  type    = string
  default = ""
  validation {
    condition     = !var.enabled || !var.enable_app || can(regex("^arn:aws:sns:[a-z0-9-]+:${var.account_id}:[A-Za-z0-9_-]+$", var.app_alarm_topic_arn))
    error_message = "Supply a reviewed account-owned SNS topic for private app health alerts."
  }
}
resource "aws_budgets_budget" "project" {
  count        = var.enabled ? 1 : 0
  account_id   = var.account_id
  name         = "pokermind-monthly"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_unit   = "USD"
  limit_amount = tostring(jsondecode(file("${path.module}/../../configs/cost-policy.json")).monthly_budget_usd)
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$pokermind"]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_notification_emails
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_notification_emails
  }
}
resource "aws_cloudwatch_metric_alarm" "app_status" {
  count               = local.count
  alarm_name          = "pokermind-private-app-status"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.app[0].id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.app_alarm_topic_arn]
  ok_actions          = [var.app_alarm_topic_arn]
}
