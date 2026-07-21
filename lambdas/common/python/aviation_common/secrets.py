"""
Cached Secrets Manager access for the AviationStack API key.

Lambda execution environments are frozen and reused across warm-start
invocations, so a module-level cache means we call Secrets Manager
roughly once per cold start instead of once per (10-minute) invocation.
"""
import json
import os
from typing import Optional

import boto3

_cached_api_key: Optional[str] = None


def get_api_key() -> str:
    global _cached_api_key
    if _cached_api_key:
        return _cached_api_key

    secret_arn = os.environ["API_KEY_SECRET_ARN"]
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])
    _cached_api_key = secret["api_key"]
    return _cached_api_key
