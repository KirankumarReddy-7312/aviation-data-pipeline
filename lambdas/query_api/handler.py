"""
Lambda 4 -- Query API (Serving layer reader)

Backs an API Gateway REST API in front of AviationMasterTable. This is the
piece meant for a chatbot backend (LLM function/tool calling) or any app
needing single-digit-millisecond lookups -- BI/analytics workloads should
go through Athena over the Silver CSV layer instead (see athena.tf), since
scanning a whole entity here would mean an expensive/slow DynamoDB Scan.

Routes (API Gateway resource paths, not a proxy+ catch-all, so each maps
to an explicit handler and API Gateway validates the path shape before
Lambda ever runs):
    GET /health
    GET /flights/{flight_iata}?date=YYYY-MM-DD           (default: today UTC)
    GET /airlines/{iata}/flights?date=YYYY-MM-DD&limit=&next_token=
    GET /airports/{iata}
    GET /airlines/{iata}
    GET /aircraft-types/{iata}
    GET /cities/{iata}
    GET /countries/{iso2}
    GET /routes/{flight_iata}
    GET /airplanes/{registration_number}
"""
import base64
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from aviation_common.logging_utils import emit_metric, log_event

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["DYNAMODB_TABLE"]
table = dynamodb.Table(TABLE_NAME)

# Internal DynamoDB bookkeeping attributes stripped from every API response.
_INTERNAL_FIELDS = {"PK", "SK", "GSI1PK", "GSI1SK", "source_key"}

# path resource -> DynamoDB PK prefix, for the generic metadata lookups.
_METADATA_ENTITY_PREFIX = {
    "/airports/{iata}": "AIRPORT",
    "/airlines/{iata}": "AIRLINE",
    "/aircraft-types/{iata}": "AIRCRAFT",
    "/cities/{iata}": "CITY",
    "/countries/{iso2}": "COUNTRY",
    "/routes/{flight_iata}": "ROUTE",
    "/airplanes/{registration_number}": "AIRPLANE",
}

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def lambda_handler(event, context):
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    try:
        if resource == "/health":
            return _response(200, {"status": "ok", "table": TABLE_NAME})

        if resource == "/flights/{flight_iata}":
            return _get_flight(path_params["flight_iata"], query_params)

        if resource == "/airlines/{iata}/flights":
            return _get_airline_flights(path_params["iata"], query_params)

        if resource in _METADATA_ENTITY_PREFIX:
            id_param = next(iter(path_params.values()))
            return _get_metadata(_METADATA_ENTITY_PREFIX[resource], id_param)

        return _response(404, {"error": f"No handler for resource '{resource}'"})

    except KeyError as exc:
        return _response(400, {"error": f"Missing required parameter: {exc}"})
    except Exception as exc:  # noqa: BLE001
        log_event("ERROR", "Unhandled query API error", resource=resource, error=str(exc))
        emit_metric("QueryApiError", 1, dimensions={"Resource": resource})
        return _response(500, {"error": "Internal error"})


def _get_flight(flight_iata: str, query_params: dict) -> dict:
    date = query_params.get("date") or _today()
    if not _is_valid_date(date):
        return _response(400, {"error": "date must be YYYY-MM-DD"})

    result = table.get_item(Key={"PK": f"FLIGHT#{flight_iata}", "SK": f"DATE#{date}"})
    item = result.get("Item")
    emit_metric("QueryFlight", 1, dimensions={"Found": str(bool(item))})
    if not item:
        return _response(404, {"error": f"No flight {flight_iata} on {date}"})
    return _response(200, _clean_item(item))


def _get_airline_flights(airline_iata: str, query_params: dict) -> dict:
    date = query_params.get("date") or _today()
    if not _is_valid_date(date):
        return _response(400, {"error": "date must be YYYY-MM-DD"})

    limit = min(int(query_params.get("limit", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    exclusive_start_key = _decode_token(query_params.get("next_token"))

    query_kwargs = {
        "IndexName": "GSI1",
        "KeyConditionExpression": Key("GSI1PK").eq(f"AIRLINE#{airline_iata}")
        & Key("GSI1SK").eq(f"DATE#{date}"),
        "Limit": limit,
    }
    if exclusive_start_key:
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key

    result = table.query(**query_kwargs)
    items = [_clean_item(item) for item in result.get("Items", [])]
    emit_metric("QueryAirlineFlights", len(items), dimensions={"Airline": airline_iata})

    body = {"flights": items, "count": len(items)}
    if "LastEvaluatedKey" in result:
        body["next_token"] = _encode_token(result["LastEvaluatedKey"])
    return _response(200, body)


def _get_metadata(prefix: str, identifier: str) -> dict:
    result = table.get_item(Key={"PK": f"{prefix}#{identifier}", "SK": "METADATA"})
    item = result.get("Item")
    emit_metric("QueryMetadata", 1, dimensions={"Entity": prefix, "Found": str(bool(item))})
    if not item:
        return _response(404, {"error": f"No {prefix.lower()} record for '{identifier}'"})
    return _response(200, _clean_item(item))


def _clean_item(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _INTERNAL_FIELDS}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _is_valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _encode_token(last_evaluated_key: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(last_evaluated_key, default=str).encode()).decode()


def _decode_token(token):
    if not token:
        return None
    return json.loads(base64.urlsafe_b64decode(token.encode()).decode())


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj) if obj % 1 else int(obj)
        return super().default(obj)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, cls=_DecimalEncoder),
    }
