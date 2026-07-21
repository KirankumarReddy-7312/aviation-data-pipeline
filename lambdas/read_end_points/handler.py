"""
Lambda 1 -- API Extraction (Bronze layer writer)

Fetches one or more AviationStack endpoints and writes the complete,
untransformed JSON response(s) to s3://<bucket>/raw/<entity>/<ts>.json.

Invoked by two EventBridge rules with different payloads:
  - every 12 hours: {"entities": ["flights"]}
  - once a month:   {"entities": ["airports", "airlines", "aircraft_types", "cities", "countries"]}
A manual/backfill invocation with no "entities" key fetches everything.

Schedule + max_pages defaults are sized for AviationStack's FREE plan (100
requests/month, total, across every endpoint) -- see
terraform/variables.tf (flights_schedule_expression /
reference_schedule_expression) for the exact budget math. A manual invoke
that omits "max_pages" also falls back to that same conservative default
(AVIATIONSTACK_MAX_PAGES), so an ad-hoc test can't accidentally blow the
monthly quota.

Design choices vs. the original doc:
  - API key comes from Secrets Manager (cached per warm start), never a
    plaintext env var.
  - Each endpoint is paginated (AviationStack caps each response page at
    `limit`), bounded by AVIATIONSTACK_MAX_PAGES and by the Lambda's own
    remaining time budget, so a single invocation can't run past its
    timeout trying to pull hundreds of thousands of flight records.
  - One entity failing (bad response, exhausted retries) does not abort
    the others -- each is isolated, logged, and reflected in a per-entity
    CloudWatch EMF metric so alarms can fire without failing the whole
    invocation and needlessly re-fetching entities that already succeeded.
"""
import json
import os
from datetime import datetime, timezone

import boto3

from aviation_common.config import DEFAULT_FETCH_ENTITIES, ENTITY_CONFIG
from aviation_common.http import ApiError, fetch_paginated
from aviation_common.logging_utils import emit_metric, log_event
from aviation_common.secrets import get_api_key

s3 = boto3.client("s3")

BUCKET = os.environ["S3_BUCKET"]
BASE_URL = os.environ.get("AVIATIONSTACK_BASE_URL", "https://api.aviationstack.com/v1")
PAGE_SIZE = int(os.environ.get("AVIATIONSTACK_PAGE_SIZE", "100"))
DEFAULT_MAX_PAGES = int(os.environ.get("AVIATIONSTACK_MAX_PAGES", "5"))


def lambda_handler(event, context):
    entities = event.get("entities") or DEFAULT_FETCH_ENTITIES
    unknown = [e for e in entities if e not in ENTITY_CONFIG]
    if unknown:
        log_event("WARNING", "Ignoring unknown entities in event payload", unknown=unknown)
        entities = [e for e in entities if e in ENTITY_CONFIG]

    # The same function serves two EventBridge schedules (flights every 10
    # min vs. reference data once a day) with different pagination budgets,
    # so max_pages travels in the event payload rather than a fixed env var.
    max_pages = int(event.get("max_pages", DEFAULT_MAX_PAGES))

    api_key = get_api_key()
    timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S")

    results = {}
    for entity in entities:
        try:
            record_count = _extract_entity(entity, api_key, timestamp, context, max_pages)
            results[entity] = {"status": "success", "records": record_count}
            emit_metric("ExtractionSuccess", 1, dimensions={"Entity": entity})
            emit_metric("RecordsExtracted", record_count, dimensions={"Entity": entity})
        except ApiError as exc:
            log_event("ERROR", "Failed to extract entity", entity=entity, error=str(exc))
            results[entity] = {"status": "error", "error": str(exc)}
            emit_metric("ExtractionFailure", 1, dimensions={"Entity": entity})
        except Exception as exc:  # noqa: BLE001 -- isolate unexpected failures per entity too
            log_event("ERROR", "Unexpected error extracting entity", entity=entity, error=str(exc))
            results[entity] = {"status": "error", "error": str(exc)}
            emit_metric("ExtractionFailure", 1, dimensions={"Entity": entity})

    failures = [e for e, r in results.items() if r["status"] == "error"]
    log_event(
        "INFO", "Extraction run complete",
        entities=entities, succeeded=len(entities) - len(failures), failed=failures,
    )
    return {"status": "completed", "results": results}


def _extract_entity(entity: str, api_key: str, timestamp: str, context, max_pages: int) -> int:
    def build_url(offset: int, limit: int) -> str:
        return (
            f"{BASE_URL}/{entity}"
            f"?access_key={api_key}&limit={limit}&offset={offset}"
        )

    def seconds_remaining() -> float:
        if context is None or not hasattr(context, "get_remaining_time_in_millis"):
            return float("inf")
        return context.get_remaining_time_in_millis() / 1000.0

    records = fetch_paginated(
        build_url,
        page_size=PAGE_SIZE,
        max_pages=max_pages,
        seconds_remaining_fn=seconds_remaining,
    )

    key = f"raw/{entity}/{timestamp}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(
            {"entity": entity, "fetched_at": timestamp, "data": records}, default=str
        ).encode("utf-8"),
        ContentType="application/json",
    )
    log_event("INFO", "Stored raw file", entity=entity, key=key, record_count=len(records))
    return len(records)
