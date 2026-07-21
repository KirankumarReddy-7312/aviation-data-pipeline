# Query API -- fronts the query_api Lambda for chatbot/app consumption of
# the DynamoDB serving layer. Requires an API key (see api_key output);
# meant to be called from a backend service (chatbot server, BI middleware),
# not directly from browser JS, so CORS preflight (OPTIONS) isn't wired up --
# add it if you need a browser frontend to call this directly.

resource "aws_api_gateway_rest_api" "query_api" {
  name        = "${local.name_prefix}-query-api"
  description = "Read-only query API over AviationMasterTable for chatbot/app consumption."
  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_lambda_permission" "apigw_invoke_query_api" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.query_api.execution_arn}/*/*"
}

# entity path segment -> {param}. Same resource/method/integration shape for
# all of them; only the Lambda-side routing (by `resource` string) differs,
# so a single for_each builds the whole flat set instead of repeating this
# 4-resource block by hand eight times.
locals {
  single_lookup_resources = {
    flights          = "flight_iata"
    airports         = "iata"
    airlines         = "iata"
    "aircraft-types" = "iata"
    cities           = "iata"
    countries        = "iso2"
    routes           = "flight_iata"
    airplanes        = "registration_number"
  }
}

resource "aws_api_gateway_resource" "entity" {
  for_each    = local.single_lookup_resources
  rest_api_id = aws_api_gateway_rest_api.query_api.id
  parent_id   = aws_api_gateway_rest_api.query_api.root_resource_id
  path_part   = each.key
}

resource "aws_api_gateway_resource" "entity_id" {
  for_each    = local.single_lookup_resources
  rest_api_id = aws_api_gateway_rest_api.query_api.id
  parent_id   = aws_api_gateway_resource.entity[each.key].id
  path_part   = "{${each.value}}"
}

resource "aws_api_gateway_method" "entity_get" {
  for_each         = local.single_lookup_resources
  rest_api_id      = aws_api_gateway_rest_api.query_api.id
  resource_id      = aws_api_gateway_resource.entity_id[each.key].id
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = true
  request_parameters = {
    "method.request.path.${each.value}" = true
  }
}

resource "aws_api_gateway_integration" "entity_get" {
  for_each                = local.single_lookup_resources
  rest_api_id             = aws_api_gateway_rest_api.query_api.id
  resource_id             = aws_api_gateway_resource.entity_id[each.key].id
  http_method             = aws_api_gateway_method.entity_get[each.key].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query_api.invoke_arn
}

# GET /airlines/{iata}/flights -- nested under the "airlines" entity_id resource.
resource "aws_api_gateway_resource" "airline_flights" {
  rest_api_id = aws_api_gateway_rest_api.query_api.id
  parent_id   = aws_api_gateway_resource.entity_id["airlines"].id
  path_part   = "flights"
}

resource "aws_api_gateway_method" "airline_flights_get" {
  rest_api_id      = aws_api_gateway_rest_api.query_api.id
  resource_id      = aws_api_gateway_resource.airline_flights.id
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = true
  request_parameters = {
    "method.request.path.iata"              = true
    "method.request.querystring.date"       = false
    "method.request.querystring.limit"      = false
    "method.request.querystring.next_token" = false
  }
}

resource "aws_api_gateway_integration" "airline_flights_get" {
  rest_api_id             = aws_api_gateway_rest_api.query_api.id
  resource_id             = aws_api_gateway_resource.airline_flights.id
  http_method             = aws_api_gateway_method.airline_flights_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query_api.invoke_arn
}

# GET /health -- no API key, cheap uptime check for external monitors.
resource "aws_api_gateway_resource" "health" {
  rest_api_id = aws_api_gateway_rest_api.query_api.id
  parent_id   = aws_api_gateway_rest_api.query_api.root_resource_id
  path_part   = "health"
}

resource "aws_api_gateway_method" "health_get" {
  rest_api_id      = aws_api_gateway_rest_api.query_api.id
  resource_id      = aws_api_gateway_resource.health.id
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = false
}

resource "aws_api_gateway_integration" "health_get" {
  rest_api_id             = aws_api_gateway_rest_api.query_api.id
  resource_id             = aws_api_gateway_resource.health.id
  http_method             = aws_api_gateway_method.health_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.query_api.invoke_arn
}

resource "aws_api_gateway_deployment" "query_api" {
  rest_api_id = aws_api_gateway_rest_api.query_api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.entity,
      aws_api_gateway_resource.entity_id,
      aws_api_gateway_method.entity_get,
      aws_api_gateway_integration.entity_get,
      aws_api_gateway_resource.airline_flights,
      aws_api_gateway_method.airline_flights_get,
      aws_api_gateway_integration.airline_flights_get,
      aws_api_gateway_resource.health,
      aws_api_gateway_method.health_get,
      aws_api_gateway_integration.health_get,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "v1" {
  deployment_id = aws_api_gateway_deployment.query_api.id
  rest_api_id   = aws_api_gateway_rest_api.query_api.id
  stage_name    = "v1"

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway_access.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      requestTime      = "$context.requestTime"
      httpMethod       = "$context.httpMethod"
      resourcePath     = "$context.resourcePath"
      status           = "$context.status"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integration.error"
    })
  }
}

resource "aws_cloudwatch_log_group" "api_gateway_access" {
  name              = "/aws/apigateway/${local.name_prefix}-query-api"
  retention_in_days = var.log_retention_days
}

resource "aws_api_gateway_method_settings" "v1" {
  rest_api_id = aws_api_gateway_rest_api.query_api.id
  stage_name  = aws_api_gateway_stage.v1.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
    metrics_enabled        = true
    logging_level          = "INFO"
  }
}

resource "aws_api_gateway_api_key" "chatbot" {
  name = "${local.name_prefix}-chatbot-client"
}

resource "aws_api_gateway_usage_plan" "chatbot" {
  name = "${local.name_prefix}-chatbot-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.query_api.id
    stage  = aws_api_gateway_stage.v1.stage_name
  }

  # Backstop against a runaway chatbot/agent retry loop hammering the API.
  throttle_settings {
    burst_limit = 20
    rate_limit  = 10
  }
  quota_settings {
    limit  = 100000
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "chatbot" {
  key_id        = aws_api_gateway_api_key.chatbot.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.chatbot.id
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name_prefix}-query-api-5xx"
  alarm_description   = "Query API is returning server errors."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  dimensions          = { ApiName = aws_api_gateway_rest_api.query_api.name, Stage = aws_api_gateway_stage.v1.stage_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
