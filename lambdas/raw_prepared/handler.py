"""
Lambda 2 -- JSON to CSV Transformation (Silver layer writer)

Triggered by S3 ObjectCreated events under raw/*.json. For every S3
record in the event: reads the raw JSON, looks up the entity's
extract() function from the shared config, flattens each record,
drops rows missing required fields, and writes a clean CSV to
prepared/<entity>/<same path>.csv.

Unlike the original doc's handler (which only implemented flights and
airports and returned early on the first record), this drives all 6
entities off ENTITY_CONFIG and processes every record in the event.
"""
import csv
import io
import json
import time
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

from aviation_common.config import ENTITY_CONFIG
from aviation_common.logging_utils import emit_metric, log_event

s3 = boto3.client("s3")

# Read-your-writes on S3 is strongly consistent today, but a get_object
# racing an eventually-visible put from a different region/replica is
# still worth a couple of quick retries rather than failing the whole batch.
GET_OBJECT_MAX_RETRIES = 3


def lambda_handler(event, context):
    processed, skipped, failed = 0, 0, 0

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.startswith("raw/") or not key.endswith(".json"):
            log_event("WARNING", "Ignoring non-raw-json key", key=key)
            skipped += 1
            continue

        entity = key.split("/")[1]
        config = ENTITY_CONFIG.get(entity)
        if config is None:
            log_event("WARNING", "Unknown entity in raw key, skipping", key=key, entity=entity)
            skipped += 1
            emit_metric("TransformUnknownEntity", 1, dimensions={"Entity": entity})
            continue

        try:
            row_count, dropped = _transform_object(bucket, key, entity, config)
            processed += 1
            emit_metric("TransformSuccess", 1, dimensions={"Entity": entity})
            emit_metric("RowsWritten", row_count, dimensions={"Entity": entity})
            if dropped:
                emit_metric("RowsDropped", dropped, dimensions={"Entity": entity})
            log_event(
                "INFO", "Transformed raw file to CSV",
                key=key, entity=entity, rows_written=row_count, rows_dropped=dropped,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            emit_metric("TransformFailure", 1, dimensions={"Entity": entity})
            log_event("ERROR", "Failed to transform file", key=key, entity=entity, error=str(exc))
            # Re-raise so this async invocation is marked failed and routes to
            # the DLQ -- the S3 object that caused it is preserved for replay.
            raise

    return {"processed": processed, "skipped": skipped, "failed": failed}


def _transform_object(bucket: str, key: str, entity: str, config: dict) -> tuple[int, int]:
    data = _get_object_with_retry(bucket, key)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=config["columns"], extrasaction="ignore")
    writer.writeheader()

    rows_written = 0
    rows_dropped = 0
    for raw_record in data.get("data", []):
        row = config["extract"](raw_record)
        if any(not row.get(field) for field in config["required"]):
            rows_dropped += 1
            continue
        writer.writerow(row)
        rows_written += 1

    prepared_key = key.replace("raw/", "prepared/", 1).rsplit(".json", 1)[0] + ".csv"
    s3.put_object(
        Bucket=bucket,
        Key=prepared_key,
        Body=output.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )
    return rows_written, rows_dropped


def _get_object_with_retry(bucket: str, key: str) -> dict:
    last_error = None
    for attempt in range(GET_OBJECT_MAX_RETRIES):
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            return json.loads(obj["Body"].read())
        except ClientError as exc:
            last_error = exc
            if exc.response.get("Error", {}).get("Code") not in {"NoSuchKey", "SlowDown"}:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise last_error
