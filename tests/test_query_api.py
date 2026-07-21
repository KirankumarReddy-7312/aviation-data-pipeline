import json
from decimal import Decimal

import boto3
import pytest

from conftest import import_lambda_module

TABLE = "AviationMasterTable"


@pytest.fixture
def handler_module(monkeypatch):
    # Must set the env var before import_lambda_module executes handler.py's
    # module-level `os.environ["DYNAMODB_TABLE"]` read.
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
    return import_lambda_module("query_api")


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)
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
    t = dynamodb.Table(TABLE)
    t.put_item(Item={
        "PK": "FLIGHT#CA4127", "SK": "DATE#2026-02-11",
        "GSI1PK": "AIRLINE#CA", "GSI1SK": "DATE#2026-02-11",
        "airline_iata": "CA", "flight_status": "scheduled", "entity_type": "flights",
        "source_key": "prepared/flights/2026/02/11/x.csv",
    })
    t.put_item(Item={
        "PK": "AIRPORT#DEL", "SK": "METADATA",
        "airport_name": "Indira Gandhi International", "latitude": Decimal("28.5665"),
        "entity_type": "airports",
    })
    return t


def _event(resource, path_params=None, query_params=None):
    return {
        "resource": resource,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
    }


@pytest.mark.parametrize("resource", ["/health"])
def test_health_check(handler_module, resource):
    response = handler_module.lambda_handler(_event(resource), None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "ok"


def test_get_flight_found(handler_module, table):
    response = handler_module.lambda_handler(
        _event("/flights/{flight_iata}", {"flight_iata": "CA4127"}, {"date": "2026-02-11"}), None
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["flight_status"] == "scheduled"
    # internal DynamoDB bookkeeping keys must never leak into the API response
    assert "PK" not in body and "SK" not in body and "source_key" not in body


def test_get_flight_not_found(handler_module, table):
    response = handler_module.lambda_handler(
        _event("/flights/{flight_iata}", {"flight_iata": "ZZ9999"}, {"date": "2026-02-11"}), None
    )
    assert response["statusCode"] == 404


def test_get_flight_rejects_bad_date(handler_module, table):
    response = handler_module.lambda_handler(
        _event("/flights/{flight_iata}", {"flight_iata": "CA4127"}, {"date": "not-a-date"}), None
    )
    assert response["statusCode"] == 400


def test_get_airport_metadata(handler_module, table):
    response = handler_module.lambda_handler(
        _event("/airports/{iata}", {"iata": "DEL"}), None
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["airport_name"] == "Indira Gandhi International"
    assert body["latitude"] == 28.5665  # Decimal serialized to a plain JSON number


def test_get_airline_flights_by_gsi1(handler_module, table):
    response = handler_module.lambda_handler(
        _event("/airlines/{iata}/flights", {"iata": "CA"}, {"date": "2026-02-11"}), None
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["count"] == 1
    assert body["flights"][0]["airline_iata"] == "CA"


def test_unknown_resource_returns_404(handler_module, table):
    response = handler_module.lambda_handler(_event("/not/a/real/resource"), None)
    assert response["statusCode"] == 404
