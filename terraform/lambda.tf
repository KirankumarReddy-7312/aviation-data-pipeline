# Shared code (aviation_common) ships once as a Lambda Layer instead of being
# copied into all 3 function zips -- keeps the entity config, HTTP retry
# client, and key-building logic identical across extract/transform/load.

data "archive_file" "common_layer" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/common"
  output_path = "${path.module}/build/common_layer.zip"
}

resource "aws_lambda_layer_version" "common" {
  layer_name          = "${local.name_prefix}-common"
  filename            = data.archive_file.common_layer.output_path
  source_code_hash    = data.archive_file.common_layer.output_base64sha256
  compatible_runtimes = ["python3.12"]
}

data "archive_file" "read_end_points" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/read_end_points"
  output_path = "${path.module}/build/read_end_points.zip"
}

data "archive_file" "raw_prepared" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/raw_prepared"
  output_path = "${path.module}/build/raw_prepared.zip"
}

data "archive_file" "prepared_dynamodb" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/prepared_dynamodb"
  output_path = "${path.module}/build/prepared_dynamodb.zip"
}

data "archive_file" "query_api" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/query_api"
  output_path = "${path.module}/build/query_api.zip"
}

resource "aws_lambda_function" "read_end_points" {
  function_name    = "${local.name_prefix}-read-end-points"
  role             = aws_iam_role.extract.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 256
  timeout          = 120
  filename         = data.archive_file.read_end_points.output_path
  source_code_hash = data.archive_file.read_end_points.output_base64sha256
  layers           = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      S3_BUCKET               = aws_s3_bucket.data_lake.bucket
      API_KEY_SECRET_ARN      = aws_secretsmanager_secret.aviationstack_api_key.arn
      AVIATIONSTACK_BASE_URL  = var.aviationstack_base_url
      AVIATIONSTACK_PAGE_SIZE = tostring(var.aviationstack_page_size)
      AVIATIONSTACK_MAX_PAGES = tostring(var.flights_max_pages)
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.extract_dlq.arn
  }

  depends_on = [aws_cloudwatch_log_group.extract]
}

resource "aws_lambda_function" "raw_prepared" {
  function_name    = "${local.name_prefix}-raw-prepared"
  role             = aws_iam_role.transform.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 512
  timeout          = 300
  filename         = data.archive_file.raw_prepared.output_path
  source_code_hash = data.archive_file.raw_prepared.output_base64sha256
  layers           = [aws_lambda_layer_version.common.arn]

  dead_letter_config {
    target_arn = aws_sqs_queue.transform_dlq.arn
  }

  depends_on = [aws_cloudwatch_log_group.transform]
}

resource "aws_lambda_function" "prepared_dynamodb" {
  function_name    = "${local.name_prefix}-prepared-dynamodb"
  role             = aws_iam_role.load.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 512
  timeout          = 300
  filename         = data.archive_file.prepared_dynamodb.output_path
  source_code_hash = data.archive_file.prepared_dynamodb.output_base64sha256
  layers           = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.aviation_master.name
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.load_dlq.arn
  }

  depends_on = [aws_cloudwatch_log_group.load]
}

# Synchronous, request/response -- no DLQ (API Gateway callers get the error
# directly and can retry themselves; there's nothing async to redrive).
resource "aws_lambda_function" "query_api" {
  function_name    = "${local.name_prefix}-query-api"
  role             = aws_iam_role.query_api.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  memory_size      = 256
  timeout          = 10
  filename         = data.archive_file.query_api.output_path
  source_code_hash = data.archive_file.query_api.output_base64sha256
  layers           = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.aviation_master.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.query_api]
}
