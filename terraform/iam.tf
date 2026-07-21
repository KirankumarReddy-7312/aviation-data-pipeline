# Three separate execution roles (one per Lambda) instead of one shared role --
# each function only gets the S3 prefixes, DynamoDB actions, and DLQ it
# actually touches.

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---------- Lambda 1: read_end_points ----------

resource "aws_iam_role" "extract" {
  name               = "${local.name_prefix}-extract-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "extract" {
  statement {
    sid       = "WriteBronzeLayer"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/raw/*"]
  }
  statement {
    sid       = "ReadApiKeySecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.aviationstack_api_key.arn]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.extract.arn}:*"]
  }
  statement {
    sid       = "SendToOwnDlq"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.extract_dlq.arn]
  }
}

resource "aws_iam_role_policy" "extract" {
  name   = "${local.name_prefix}-extract-policy"
  role   = aws_iam_role.extract.id
  policy = data.aws_iam_policy_document.extract.json
}

# ---------- Lambda 2: raw_prepared ----------

resource "aws_iam_role" "transform" {
  name               = "${local.name_prefix}-transform-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "transform" {
  statement {
    sid       = "ReadBronzeLayer"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/raw/*"]
  }
  statement {
    sid       = "WriteSilverLayer"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/prepared/*"]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.transform.arn}:*"]
  }
  statement {
    sid       = "SendToOwnDlq"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.transform_dlq.arn]
  }
}

resource "aws_iam_role_policy" "transform" {
  name   = "${local.name_prefix}-transform-policy"
  role   = aws_iam_role.transform.id
  policy = data.aws_iam_policy_document.transform.json
}

# ---------- Lambda 3: prepared_dynamodb ----------

resource "aws_iam_role" "load" {
  name               = "${local.name_prefix}-load-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "load" {
  statement {
    sid       = "ReadSilverLayer"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/prepared/*"]
  }
  statement {
    sid       = "WriteServingLayer"
    actions   = ["dynamodb:BatchWriteItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.aviation_master.arn]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.load.arn}:*"]
  }
  statement {
    sid       = "SendToOwnDlq"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.load_dlq.arn]
  }
}

resource "aws_iam_role_policy" "load" {
  name   = "${local.name_prefix}-load-policy"
  role   = aws_iam_role.load.id
  policy = data.aws_iam_policy_document.load.json
}

# ---------- Lambda 4: query_api ----------

resource "aws_iam_role" "query_api" {
  name               = "${local.name_prefix}-query-api-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "query_api" {
  statement {
    sid     = "ReadServingLayer"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.aviation_master.arn,
      "${aws_dynamodb_table.aviation_master.arn}/index/*",
    ]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.query_api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "query_api" {
  name   = "${local.name_prefix}-query-api-policy"
  role   = aws_iam_role.query_api.id
  policy = data.aws_iam_policy_document.query_api.json
}
