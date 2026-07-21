import json
from unittest.mock import patch

import boto3
import pytest

from conftest import import_lambda_module

BUCKET = "test-aviation-bucket"


@pytest.fixture
def api_key_secret():
    client = boto3.client("secretsmanager", region_name="us-east-1")
    secret = client.create_secret(
        Name="aviation/aviationstack-api-key",
        SecretString=json.dumps({"api_key": "test-key-123"}),
    )
    return secret["ARN"]


@pytest.fixture
def bucket():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    return BUCKET


@pytest.fixture
def handler_module(bucket, api_key_secret, monkeypatch):
    # Env vars must be set before handler.py's module-level `os.environ[...]`
    # reads run, so this fixture depends on (and runs after) bucket/api_key_secret
    # rather than the test body setting them post-import.
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setenv("API_KEY_SECRET_ARN", api_key_secret)
    return import_lambda_module("read_end_points")


class _FakeContext:
    def get_remaining_time_in_millis(self):
        return 60000


def test_lambda_handler_writes_raw_json_per_entity(handler_module, bucket):
    fake_records = {
        "flights": [{"flight": {"iata": "CA4127"}, "flight_date": "2026-02-11"}],
        "airports": [{"iata_code": "DEL"}],
    }

    def fake_fetch_paginated(build_url, **kwargs):
        # entity name is embedded in the URL path segment right after the base URL
        for entity, records in fake_records.items():
            if f"/{entity}?" in build_url(0, 100):
                return records
        return []

    with patch(f"{handler_module.__name__}.fetch_paginated", side_effect=fake_fetch_paginated):
        result = handler_module.lambda_handler({"entities": ["flights", "airports"]}, _FakeContext())

    assert result["status"] == "completed"
    assert result["results"]["flights"] == {"status": "success", "records": 1}
    assert result["results"]["airports"] == {"status": "success", "records": 1}

    s3 = boto3.client("s3", region_name="us-east-1")
    listing = s3.list_objects_v2(Bucket=bucket, Prefix="raw/")
    keys = [obj["Key"] for obj in listing["Contents"]]
    assert any(k.startswith("raw/flights/") and k.endswith(".json") for k in keys)
    assert any(k.startswith("raw/airports/") and k.endswith(".json") for k in keys)

    flight_key = next(k for k in keys if k.startswith("raw/flights/"))
    body = json.loads(s3.get_object(Bucket=bucket, Key=flight_key)["Body"].read())
    assert body["entity"] == "flights"
    assert body["data"] == fake_records["flights"]


def test_lambda_handler_isolates_per_entity_failures(handler_module, bucket):
    from aviation_common.http import ApiError

    def fake_fetch_paginated(build_url, **kwargs):
        if "/flights?" in build_url(0, 100):
            raise ApiError("simulated upstream failure")
        return [{"iata_code": "DEL"}]

    with patch(f"{handler_module.__name__}.fetch_paginated", side_effect=fake_fetch_paginated):
        result = handler_module.lambda_handler({"entities": ["flights", "airports"]}, _FakeContext())

    assert result["results"]["flights"]["status"] == "error"
    assert result["results"]["airports"]["status"] == "success"


def test_lambda_handler_ignores_unknown_entities(handler_module, bucket):
    with patch(f"{handler_module.__name__}.fetch_paginated", return_value=[]):
        result = handler_module.lambda_handler({"entities": ["flights", "not_a_real_entity"]}, _FakeContext())

    assert set(result["results"].keys()) == {"flights"}


def test_default_entities_exclude_unverified_schemas(handler_module, bucket):
    with patch(f"{handler_module.__name__}.fetch_paginated", return_value=[]):
        result = handler_module.lambda_handler({}, _FakeContext())

    assert "routes" not in result["results"]
    assert "airplanes" not in result["results"]
    assert "flights" in result["results"]
