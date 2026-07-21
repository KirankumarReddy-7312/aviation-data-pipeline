import csv
import io
import json

import boto3
import pytest

from conftest import import_lambda_module

BUCKET = "test-aviation-bucket"


@pytest.fixture
def handler_module():
    return import_lambda_module("raw_prepared")


@pytest.fixture
def bucket():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    return BUCKET


def _s3_event(key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


def _put_raw(entity: str, records: list, key="2026/02/11/210001.json") -> str:
    s3 = boto3.client("s3", region_name="us-east-1")
    full_key = f"raw/{entity}/{key}"
    s3.put_object(Bucket=BUCKET, Key=full_key, Body=json.dumps({"data": records}).encode())
    return full_key


def test_transforms_flights_json_to_csv(handler_module, bucket):
    raw_key = _put_raw("flights", [
        {
            "flight_date": "2026-02-11",
            "flight_status": "scheduled",
            "departure": {"iata": "PEK"},
            "arrival": {"iata": "LZY"},
            "airline": {"name": "Air China LTD", "iata": "CA"},
            "flight": {"iata": "CA4127"},
            "aircraft": {"icao24": "780356"},
        }
    ])

    result = handler_module.lambda_handler(_s3_event(raw_key), None)
    assert result == {"processed": 1, "skipped": 0, "failed": 0}

    s3 = boto3.client("s3", region_name="us-east-1")
    csv_body = s3.get_object(Bucket=BUCKET, Key="prepared/flights/2026/02/11/210001.csv")["Body"].read().decode()
    rows = list(csv.DictReader(io.StringIO(csv_body)))
    assert len(rows) == 1
    assert rows[0]["flight_iata"] == "CA4127"
    assert rows[0]["airline_name"] == "Air China LTD"


def test_drops_rows_missing_required_fields(handler_module, bucket):
    raw_key = _put_raw("flights", [
        {"flight_date": "2026-02-11", "flight": {"iata": "CA4127"}},  # valid
        {"flight_date": "2026-02-11", "flight": {}},  # missing flight_iata -> dropped
    ])

    result = handler_module.lambda_handler(_s3_event(raw_key), None)
    assert result == {"processed": 1, "skipped": 0, "failed": 0}

    s3 = boto3.client("s3", region_name="us-east-1")
    csv_body = s3.get_object(Bucket=BUCKET, Key="prepared/flights/2026/02/11/210001.csv")["Body"].read().decode()
    rows = list(csv.DictReader(io.StringIO(csv_body)))
    assert len(rows) == 1


def test_skips_unknown_entity_without_raising(handler_module, bucket):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(Bucket=BUCKET, Key="raw/not_a_real_entity/2026/02/11/x.json", Body=b'{"data": []}')

    result = handler_module.lambda_handler(_s3_event("raw/not_a_real_entity/2026/02/11/x.json"), None)
    assert result == {"processed": 0, "skipped": 1, "failed": 0}


def test_ignores_non_raw_keys(handler_module, bucket):
    result = handler_module.lambda_handler(_s3_event("prepared/flights/x.csv"), None)
    assert result == {"processed": 0, "skipped": 1, "failed": 0}


def test_all_six_original_entities_transform_without_error(handler_module, bucket):
    fixtures = {
        "airports": [{"iata_code": "DEL", "airport_name": "Indira Gandhi Intl"}],
        "airlines": [{"iata_code": "AA", "airline_name": "American Airlines"}],
        "aircraft_types": [{"iata_code": "320", "aircraft_name": "Airbus A320"}],
        "cities": [{"iata_code": "AAA", "city_name": "Anaa"}],
        "countries": [{"country_iso2": "IN", "country_name": "India"}],
    }
    for entity, records in fixtures.items():
        raw_key = _put_raw(entity, records)
        result = handler_module.lambda_handler(_s3_event(raw_key), None)
        assert result == {"processed": 1, "skipped": 0, "failed": 0}, entity
