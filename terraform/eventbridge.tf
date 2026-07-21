# Two schedules instead of one: "flights" is time-sensitive, so it gets most
# of the monthly request budget. The other 5 entities are slow-moving
# reference data (airport/airline/aircraft/city/country metadata) that
# barely changes, so they run once a month instead.
#
# Defaults are sized for AviationStack's FREE plan (100 requests/month,
# TOTAL, shared across every endpoint) -- see the descriptions on
# var.flights_schedule_expression and var.reference_schedule_expression in
# variables.tf for the exact math (65/100 used at defaults). If you're on a
# paid plan, raise the frequencies and *_max_pages variables in
# terraform.tfvars; nothing here needs to change.

resource "aws_cloudwatch_event_rule" "flights_schedule" {
  name                = "${local.name_prefix}-flights-schedule"
  description         = "Triggers read_end_points for the time-sensitive flights entity."
  schedule_expression = var.flights_schedule_expression
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "flights_schedule" {
  rule = aws_cloudwatch_event_rule.flights_schedule.name
  arn  = aws_lambda_function.read_end_points.arn
  input = jsonencode({
    source    = "scheduled"
    entities  = ["flights"]
    max_pages = var.flights_max_pages
  })
}

resource "aws_lambda_permission" "flights_schedule" {
  statement_id  = "AllowEventBridgeFlightsSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read_end_points.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.flights_schedule.arn
}

resource "aws_cloudwatch_event_rule" "reference_schedule" {
  name                = "${local.name_prefix}-reference-schedule"
  description         = "Triggers read_end_points for slow-changing reference entities."
  schedule_expression = var.reference_schedule_expression
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "reference_schedule" {
  rule = aws_cloudwatch_event_rule.reference_schedule.name
  arn  = aws_lambda_function.read_end_points.arn
  input = jsonencode({
    source    = "scheduled"
    entities  = ["airports", "airlines", "aircraft_types", "cities", "countries"]
    max_pages = var.reference_max_pages
  })
}

resource "aws_lambda_permission" "reference_schedule" {
  statement_id  = "AllowEventBridgeReferenceSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read_end_points.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reference_schedule.arn
}
