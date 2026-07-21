resource "aws_secretsmanager_secret" "aviationstack_api_key" {
  name        = "${local.name_prefix}/aviationstack-api-key"
  description = "AviationStack API access_key used by the read_end_points Lambda."
}

resource "aws_secretsmanager_secret_version" "aviationstack_api_key" {
  secret_id = aws_secretsmanager_secret.aviationstack_api_key.id
  secret_string = jsonencode({
    api_key = var.aviationstack_api_key
  })
}
