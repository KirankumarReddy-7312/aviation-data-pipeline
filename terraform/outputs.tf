output "s3_bucket_name" {
  description = "Data lake bucket holding raw/ (Bronze) and prepared/ (Silver)."
  value       = aws_s3_bucket.data_lake.bucket
}

output "dynamodb_table_name" {
  description = "Serving-layer single table."
  value       = aws_dynamodb_table.aviation_master.name
}

output "dynamodb_table_arn" {
  value = aws_dynamodb_table.aviation_master.arn
}

output "lambda_function_names" {
  value = {
    read_end_points   = aws_lambda_function.read_end_points.function_name
    raw_prepared      = aws_lambda_function.raw_prepared.function_name
    prepared_dynamodb = aws_lambda_function.prepared_dynamodb.function_name
  }
}

output "dlq_urls" {
  description = "Dead-letter queue URLs for manual failure replay -- see RUNBOOK.md."
  value = {
    extract   = aws_sqs_queue.extract_dlq.id
    transform = aws_sqs_queue.transform_dlq.id
    load      = aws_sqs_queue.load_dlq.id
  }
}

output "secrets_manager_secret_arn" {
  description = "Where the AviationStack API key lives. Rotate the key here, not in Terraform state."
  value       = aws_secretsmanager_secret.aviationstack_api_key.arn
}

output "sns_alerts_topic_arn" {
  description = "Subscribe additional endpoints (Slack webhook via SNS-to-Lambda, PagerDuty, etc.) here."
  value       = aws_sns_topic.alerts.arn
}

output "cloudwatch_dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.pipeline.dashboard_name}"
}

output "query_api_base_url" {
  description = "Base URL for the chatbot/app-facing query API. See RUNBOOK.md for the endpoint list."
  value       = aws_api_gateway_stage.v1.invoke_url
}

output "query_api_key_id" {
  description = "Retrieve the actual key value with: aws apigateway get-api-key --api-key <this-id> --include-value"
  value       = aws_api_gateway_api_key.chatbot.id
}

output "athena_workgroup_name" {
  description = "Athena workgroup for BI queries over the Silver layer."
  value       = aws_athena_workgroup.aviation.name
}

output "glue_database_name" {
  value = aws_glue_catalog_database.aviation_silver.name
}
