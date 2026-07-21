# One DLQ per Lambda so a failure is always traceable back to which stage
# broke, rather than pooling every failure into one undifferentiated queue.

resource "aws_sqs_queue" "extract_dlq" {
  name                      = "${local.name_prefix}-extract-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "transform_dlq" {
  name                      = "${local.name_prefix}-transform-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "load_dlq" {
  name                      = "${local.name_prefix}-load-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
