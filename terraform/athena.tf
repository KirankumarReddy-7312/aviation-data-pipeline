# BI consumption path: Silver-layer CSVs (prepared/<entity>/...), cataloged
# for Athena/QuickSight. This is deliberately separate from the DynamoDB
# query API -- a BI tool doing ad-hoc SQL / full scans over history should
# never point at the serving-layer table (expensive Scans, no SQL), and a
# chatbot doing point lookups shouldn't be paying Athena's per-query
# S3-scan latency for a single flight status check.
#
# NOTE: column lists here are a second copy of what lives in
# lambdas/common/python/aviation_common/config.py's ENTITY_CONFIG["columns"].
# Terraform can't import Python, so if you add/rename a Silver column there,
# update the matching entry in local.athena_entity_columns below too.

resource "aws_glue_catalog_database" "aviation_silver" {
  name = "aviation_silver_db"
}

locals {
  # entity -> ordered list of {name, type} matching the CSV header Lambda 2 writes.
  athena_entity_columns = {
    flights = [
      { name = "flight_iata", type = "string" },
      { name = "flight_date", type = "string" },
      { name = "airline_iata", type = "string" },
      { name = "airline_name", type = "string" },
      { name = "departure_iata", type = "string" },
      { name = "arrival_iata", type = "string" },
      { name = "flight_status", type = "string" },
      { name = "aircraft_icao24", type = "string" },
    ]
    airports = [
      { name = "iata_code", type = "string" },
      { name = "airport_name", type = "string" },
      { name = "country_name", type = "string" },
      { name = "latitude", type = "double" },
      { name = "longitude", type = "double" },
      { name = "timezone", type = "string" },
    ]
    airlines = [
      { name = "iata_code", type = "string" },
      { name = "airline_name", type = "string" },
      { name = "country_name", type = "string" },
      { name = "fleet_size", type = "int" },
      { name = "status", type = "string" },
    ]
    aircraft_types = [
      { name = "iata_code", type = "string" },
      { name = "aircraft_name", type = "string" },
    ]
    cities = [
      { name = "iata_code", type = "string" },
      { name = "city_name", type = "string" },
      { name = "country_iso2", type = "string" },
      { name = "latitude", type = "double" },
      { name = "longitude", type = "double" },
      { name = "timezone", type = "string" },
    ]
    countries = [
      { name = "country_iso2", type = "string" },
      { name = "country_name", type = "string" },
      { name = "continent", type = "string" },
      { name = "capital", type = "string" },
      { name = "population", type = "int" },
    ]
    routes = [
      { name = "flight_iata", type = "string" },
      { name = "airline_iata", type = "string" },
      { name = "airline_name", type = "string" },
      { name = "departure_iata", type = "string" },
      { name = "arrival_iata", type = "string" },
    ]
    airplanes = [
      { name = "registration_number", type = "string" },
      { name = "airline_iata", type = "string" },
      { name = "model_code", type = "string" },
      { name = "production_line", type = "string" },
      { name = "engines_count", type = "int" },
      { name = "engines_type", type = "string" },
      { name = "plane_status", type = "string" },
    ]
  }
}

resource "aws_glue_catalog_table" "entity" {
  for_each      = local.athena_entity_columns
  name          = each.key
  database_name = aws_glue_catalog_database.aviation_silver.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification"         = "csv"
    "skip.header.line.count" = "1"
    "areColumnsQuoted"       = "false"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data_lake.bucket}/prepared/${each.key}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
      parameters = {
        "field.delim" = ","
      }
    }

    dynamic "columns" {
      for_each = each.value
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}

resource "aws_s3_bucket" "athena_results" {
  bucket = "${local.bucket_name}-athena-results"
}

resource "aws_s3_bucket_public_access_block" "athena_results" {
  bucket                  = aws_s3_bucket.athena_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    id     = "expire-query-results"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_athena_workgroup" "aviation" {
  name = "${local.name_prefix}-bi"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/"
    }
  }
}
