resource "aws_lambda_permission" "s3_invoke_transform" {
  statement_id  = "AllowS3InvokeTransform"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.raw_prepared.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data_lake.arn
}

resource "aws_lambda_permission" "s3_invoke_load" {
  statement_id  = "AllowS3InvokeLoad"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.prepared_dynamodb.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data_lake.arn
}

# A single aws_s3_bucket_notification resource is required -- S3 only allows
# one notification configuration per bucket, so splitting this across
# multiple resources would have each apply silently overwrite the last.
resource "aws_s3_bucket_notification" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.raw_prepared.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
    filter_suffix       = ".json"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.prepared_dynamodb.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "prepared/"
    filter_suffix       = ".csv"
  }

  depends_on = [
    aws_lambda_permission.s3_invoke_transform,
    aws_lambda_permission.s3_invoke_load,
  ]
}
