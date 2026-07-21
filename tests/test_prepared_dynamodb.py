from decimal import Decimal

import boto3
import pytest

from conftest import import_lambda_module

BUCKET = "test-aviation-bucket"
TABLE = "AviationMasterTable"


@pytest.fixture
def handler_module(monkeypatch):
    # Must set the env var before import_lambda_module executes handler.py's
    # module-level `os.environ["DYNAMODB_TABLE"]` read.
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    return import_lambda_module("prepared_dynamodb")


@pytest.fixture
def table():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "GSI1",
            "KeySchema": [{"AttributeName": "GSI1PK", "KeyType": "HASH"}, {"AttributeName": "GSI1SK", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    return dynamodb.Table(TABLE)


@pytest.fixture
def bucket():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    return BUCKET


def _s3_event(key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


def _put_prepared_csv(entity: str, header: list, rows: list, key="2026/02/11/210001.csv") -> str:
    import csv
    import io
    s3 = boto3.client("s3", region_name="us-east-1")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    full_key = f"prepared/{entity}/{key}"
    s3.put_object(Bucket=BUCKET, Key=full_key, Body=output.getvalue().encode())
    return full_key


def test_loads_flights_with_gsi1_keys(handler_module, table, bucket, monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    key = _put_prepared_csv(
        "flights",
        ["flight_iata", "flight_date", "airline_iata", "airline_name", "departure_iata", "arrival_iata", "flight_status", "aircraft_icao24"],
        [["CA4127", "2026-02-11", "CA", "Air China LTD", "PEK", "LZY", "scheduled", "780356"]],
    )

    result = handler_module.lambda_handler(_s3_event(key), None)
    assert result == {"processed": 1, "skipped": 0, "failed": 0}

    item = table.get_item(Key={"PK": "FLIGHT#CA4127", "SK": "DATE#2026-02-11"})["Item"]
    assert item["airline_name"] == "Air China LTD"
    assert item["GSI1PK"] == "AIRLINE#CA"
    assert item["entity_type"] == "flights"


def test_loads_airports_with_decimal_lat_long(handler_module, table, bucket, monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    key = _put_prepared_csv(
        "airports",
        ["iata_code", "airport_name", "country_name", "latitude", "longitude", "timezone"],
        [["DEL", "Indira Gandhi International", "India", "28.5665", "77.1031", "Asia/Kolkata"]],
    )

    result = handler_module.lambda_handler(_s3_event(key), None)
    assert result == {"processed": 1, "skipped": 0, "failed": 0}

    item = table.get_item(Key={"PK": "AIRPORT#DEL", "SK": "METADATA"})["Item"]
    assert item["latitude"] == Decimal("28.5665")
    assert isinstance(item["latitude"], Decimal)


def test_drops_rows_missing_required_field(handler_module, table, bucket, monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    key = _put_prepared_csv(
        "airports",
        ["iata_code", "airport_name", "country_name", "latitude", "longitude", "timezone"],
        [["", "No Code Airport", "Nowhere", "0", "0", "UTC"]],
    )

    result = handler_module.lambda_handler(_s3_event(key), None)
    assert result == {"processed": 1, "skipped": 0, "failed": 0}

    scan = table.scan()
    assert scan["Count"] == 0


def test_query_by_airline_gsi1_returns_flight(handler_module, table, bucket, monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    key = _put_prepared_csv(
        "flights",
        ["flight_iata", "flight_date", "airline_iata", "airline_name", "departure_iata", "arrival_iata", "flight_status", "aircraft_icao24"],
        [["CA4127", "2026-02-11", "CA", "Air China LTD", "PEK", "LZY", "scheduled", "780356"]],
    )
    handler_module.lambda_handler(_s3_event(key), None)

    from boto3.dynamodb.conditions import Key
    result = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("AIRLINE#CA") & Key("GSI1SK").eq("DATE#2026-02-11"),
    )
    assert result["Count"] == 1
