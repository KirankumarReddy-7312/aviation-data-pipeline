variable "aws_region" {
  description = "AWS region to deploy the pipeline into."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name_prefix" {
  description = "Prefix for the auto-generated S3 data lake bucket name. A random suffix is appended so it stays globally unique."
  type        = string
  default     = "aviation-data-pipeline"
}

variable "aviationstack_api_key" {
  description = <<-EOT
    AviationStack API key. Marked sensitive, but Terraform still writes it into
    state in plaintext -- for a real production account, create the Secrets
    Manager secret out-of-band (or rotate the key immediately after apply) so
    the live key never lands in .tfstate. See RUNBOOK.md.
  EOT
  type        = string
  sensitive   = true
}

variable "aviationstack_base_url" {
  description = "Base URL for the AviationStack API."
  type        = string
  default     = "https://api.aviationstack.com/v1"
}

variable "flights_schedule_expression" {
  description = <<-EOT
    EventBridge schedule for the time-sensitive 'flights' entity. Default is
    sized for AviationStack's FREE plan (100 requests/month TOTAL, shared
    across every endpoint): rate(12 hours) x 1 page/run = 2 requests/day =
    60 requests/month. If you're on a paid plan, tighten this (e.g.
    "rate(10 minutes)") and raise flights_max_pages -- see
    reference_schedule_expression for the rest of the monthly budget math.
  EOT
  type        = string
  default     = "rate(12 hours)"
}

variable "reference_schedule_expression" {
  description = <<-EOT
    EventBridge schedule for the slow-changing reference entities (airports,
    airlines, aircraft_types, cities, countries). Default is monthly, not
    daily -- this data barely changes, and on the FREE plan every request
    counts against the same 100/month pool the flights schedule draws from.
    Monthly x 1 page x 5 entities = 5 requests/month. Combined with the
    60/month from flights_schedule_expression, that's 65/100 used, leaving
    ~35/month headroom for manual testing and backfills. Raise the
    frequency only if you're on a paid plan.
  EOT
  type        = string
  default     = "cron(0 3 1 * ? *)" # 03:00 UTC on the 1st of each month
}

variable "aviationstack_page_size" {
  description = "Records requested per AviationStack API page (their documented max is 100 on most plans)."
  type        = number
  default     = 100
}

variable "flights_max_pages" {
  description = <<-EOT
    Max pages Lambda 1 pulls per run for the 'flights' entity. Kept at 1 by
    default because on the FREE plan every page is a billed request against
    the 100/month cap -- see flights_schedule_expression for the full budget
    math. Raise this only on a paid plan with a larger/unlimited quota.
  EOT
  type        = number
  default     = 1
}

variable "reference_max_pages" {
  description = <<-EOT
    Max pages Lambda 1 pulls per run for reference entities. Kept at 1 by
    default for the same free-plan quota reason as flights_max_pages --
    each reference entity gets 1 page (up to aviationstack_page_size
    records) per monthly run. Raise this only on a paid plan.
  EOT
  type        = number
  default     = 1
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for all 3 Lambda log groups."
  type        = number
  default     = 30
}

variable "alarm_notification_email" {
  description = "Email address subscribed to the aviation-pipeline-alerts SNS topic. Leave empty to skip the subscription (you can still subscribe manually later)."
  type        = string
  default     = ""
}

variable "dynamodb_point_in_time_recovery" {
  description = "Enable DynamoDB point-in-time recovery (continuous backups) on AviationMasterTable."
  type        = bool
  default     = true
}

variable "raw_glacier_transition_days" {
  description = "Days after which raw/ (Bronze) objects transition to Glacier for cheap long-term archival."
  type        = number
  default     = 90
}

variable "raw_infrequent_access_days" {
  description = "Days after which raw/ (Bronze) objects transition to S3 Standard-IA."
  type        = number
  default     = 30
}

variable "prepared_expiration_days" {
  description = "Days after which prepared/ (Silver) CSVs expire. They're cheap to regenerate from raw/, so they don't need Glacier -- just a shorter lifetime."
  type        = number
  default     = 180
}
