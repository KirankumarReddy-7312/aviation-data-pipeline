#!/usr/bin/env python3
"""
Fetch one page from a live AviationStack endpoint and print the field
names of the first record, so you can compare them against the
extract() mapping in lambdas/common/python/aviation_common/config.py
before turning on an entity (especially the schema_verified=False ones:
routes, airplanes).

Usage:
    AVIATIONSTACK_API_KEY=xxxx python scripts/inspect_endpoint.py routes
    AVIATIONSTACK_API_KEY=xxxx python scripts/inspect_endpoint.py airplanes --raw
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://api.aviationstack.com/v1"


def flatten_keys(record: dict, prefix: str = "") -> list[str]:
    keys = []
    for key, value in record.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            keys.extend(flatten_keys(value, prefix=f"{full_key}."))
        else:
            keys.append(full_key)
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entity", help="AviationStack endpoint path segment, e.g. 'routes'")
    parser.add_argument("--raw", action="store_true", help="Print the full first record instead of just field names")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    api_key = os.environ.get("AVIATIONSTACK_API_KEY")
    if not api_key:
        print("Set AVIATIONSTACK_API_KEY in your environment first.", file=sys.stderr)
        return 1

    params = urllib.parse.urlencode({"access_key": api_key, "limit": args.limit})
    url = f"{BASE_URL}/{args.entity}?{params}"

    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode())

    if "error" in payload:
        print(f"API error: {payload['error']}", file=sys.stderr)
        return 1

    records = payload.get("data", [])
    if not records:
        print("No records returned -- check the endpoint name and your plan's access to it.", file=sys.stderr)
        return 1

    first = records[0]
    if args.raw:
        print(json.dumps(first, indent=2))
    else:
        for key in sorted(flatten_keys(first)):
            print(key)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
