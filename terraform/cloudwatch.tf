# Explicit log groups (rather than letting Lambda auto-create them) so
# retention is enforced from day one instead of defaulting to "never expire".

resource "aws_cloudwatch_log_group" "extract" {
  name              = "/aws/lambda/${local.name_prefix}-read-end-points"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "transform" {
  name              = "/aws/lambda/${local.name_prefix}-raw-prepared"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "load" {
  name              = "/aws/lambda/${local.name_prefix}-prepared-dynamodb"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "query_api" {
  name              = "/aws/lambda/${local.name_prefix}-query-api"
  retention_in_days = var.log_retention_days
}

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alarm_notification_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_notification_email
}

locals {
  lambda_functions = {
    extract   = aws_lambda_function.read_end_points.function_name
    transform = aws_lambda_function.raw_prepared.function_name
    load      = aws_lambda_function.prepared_dynamodb.function_name
    query_api = aws_lambda_function.query_api.function_name
  }
  dlq_queues = {
    extract   = aws_sqs_queue.extract_dlq.name
    transform = aws_sqs_queue.transform_dlq.name
    load      = aws_sqs_queue.load_dlq.name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = local.lambda_functions
  alarm_name          = "${local.name_prefix}-${each.key}-errors"
  alarm_description   = "Any invocation errors on the ${each.key} Lambda in a 5-minute window."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each            = local.lambda_functions
  alarm_name          = "${local.name_prefix}-${each.key}-throttles"
  alarm_description   = "The ${each.key} Lambda is being throttled -- concurrency limit reached."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each            = local.dlq_queues
  alarm_name          = "${local.name_prefix}-${each.key}-dlq-not-empty"
  alarm_description   = "Messages landed in the ${each.key} DLQ -- an invocation failed after exhausting retries and needs manual replay."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_throttles" {
  alarm_name          = "${local.name_prefix}-dynamodb-throttles"
  alarm_description   = "AviationMasterTable is being throttled -- unexpected given PAY_PER_REQUEST billing, worth investigating a hot partition."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ThrottledRequests"
  dimensions          = { TableName = aws_dynamodb_table.aviation_master.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${local.name_prefix}-overview"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric",
        x    = 0, y = 0, width = 12, height = 6,
        properties = {
          title   = "Lambda Invocations / Errors"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", local.lambda_functions.extract],
            ["AWS/Lambda", "Errors", "FunctionName", local.lambda_functions.extract],
            ["AWS/Lambda", "Invocations", "FunctionName", local.lambda_functions.transform],
            ["AWS/Lambda", "Errors", "FunctionName", local.lambda_functions.transform],
            ["AWS/Lambda", "Invocations", "FunctionName", local.lambda_functions.load],
            ["AWS/Lambda", "Errors", "FunctionName", local.lambda_functions.load],
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type = "metric",
        x    = 12, y = 0, width = 12, height = 6,
        properties = {
          title   = "Records Extracted / Rows Written / Items Written by Entity"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AviationPipeline", "RecordsExtracted", "Entity", "flights"],
            ["AviationPipeline", "RowsWritten", "Entity", "flights"],
            ["AviationPipeline", "ItemsWritten", "Entity", "flights"],
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type = "metric",
        x    = 0, y = 6, width = 12, height = 6,
        properties = {
          title   = "DynamoDB Consumed Capacity"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", aws_dynamodb_table.aviation_master.name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", aws_dynamodb_table.aviation_master.name],
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type = "metric",
        x    = 12, y = 6, width = 12, height = 6,
        properties = {
          title   = "DLQ Depth"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", local.dlq_queues.extract],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", local.dlq_queues.transform],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", local.dlq_queues.load],
          ]
          period = 300
          region = var.aws_region
        }
      },
    ]
  })
}
