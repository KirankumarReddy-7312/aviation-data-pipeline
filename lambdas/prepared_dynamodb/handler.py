"""
Lambda 3 -- Load to DynamoDB (Serving layer writer)

Triggered by S3 ObjectCreated events under prepared/*.csv. For every S3
record: reads the CSV, builds each item's PK/SK (+ GSI1 keys for
flights) via the entity's key_fn from the shared config, converts
numeric columns to Decimal, and batch-writes into AviationMasterTable.

Unlike the original doc's handler (flights + airports only), this
drives all 6 entities off ENTITY_CONFIG so Lambda 2 and Lambda 3 can
never disagree about column names or key patterns.
"""
import csv
import io
import os
from urllib.parse import unquote_plus

import boto3

from aviation_common.config import ENTITY_CONFIG, to_decimal
from aviation_common.logging_utils import emit_metric, log_event

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["DYNAMODB_TABLE"]
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event, context):
    processed, skipped, failed = 0, 0, 0

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.startswith("prepared/") or not key.endswith(".csv"):
            log_event("WARNING", "Ignoring non-prepared-csv key", key=key)
            skipped += 1
            continue

        entity = key.split("/")[1]
        config = ENTITY_CONFIG.get(entity)
        if config is None:
            log_event("WARNING", "Unknown entity in prepared key, skipping", key=key, entity=entity)
            skipped += 1
            emit_metric("LoadUnknownEntity", 1, dimensions={"Entity": entity})
            continue

        try:
            written, dropped = _load_object(bucket, key, entity, config)
            processed += 1
            emit_metric("LoadSuccess", 1, dimensions={"Entity": entity})
            emit_metric("ItemsWritten", written, dimensions={"Entity": entity})
            if dropped:
                emit_metric("ItemsDropped", dropped, dimensions={"Entity": entity})
            log_event(
                "INFO", "Loaded CSV into DynamoDB",
                key=key, entity=entity, items_written=written, items_dropped=dropped,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            emit_metric("LoadFailure", 1, dimensions={"Entity": entity})
            log_event("ERROR", "Failed to load file", key=key, entity=entity, error=str(exc))
            # Re-raise so the async invocation fails and routes to the DLQ --
            # the CSV that caused it stays in S3 for replay.
            raise

    return {"processed": processed, "skipped": skipped, "failed": failed}


def _load_object(bucket: str, key: str, entity: str, config: dict) -> tuple[int, int]:
    obj = s3.get_object(Bucket=bucket, Key=key)
    csv_data = obj["Body"].read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_data))

    written = 0
    dropped = 0
    with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
        for row in reader:
            if any(not row.get(field) for field in config["required"]):
                dropped += 1
                continue

            item = {k: v for k, v in row.items() if v not in (None, "")}
            for field in config["numeric_fields"]:
                if field in item:
                    decimal_value = to_decimal(item[field])
                    if decimal_value is None:
                        item.pop(field)
                    else:
                        item[field] = decimal_value

            item.update(config["key_fn"](row))
            item["entity_type"] = entity
            item["source_key"] = key

            batch.put_item(Item=item)
            written += 1

    return written, dropped
