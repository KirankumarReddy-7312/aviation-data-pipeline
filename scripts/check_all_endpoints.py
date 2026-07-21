"""
Quick access-check across all 8 entities: tells you which endpoints your
AviationStack plan can actually reach, without printing your API key.

Usage: AVIATIONSTACK_API_KEY=xxxx python scripts/check_all_endpoints.py
"""
import json
import os
import urllib.request
import urllib.parse

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]
BASE_URL = os.environ.get("AVIATIONSTACK_BASE_URL", "https://api.aviationstack.com/v1")
ENTITIES = ["flights", "airports", "airlines", "aircraft_types", "cities", "countries", "routes", "airplanes"]

for entity in ENTITIES:
    url = f"{BASE_URL}/{entity}?access_key={API_KEY}&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read())
        if "error" in body:
            print(f"{entity:15s} DENIED   - {body['error'].get('code')}: {body['error'].get('message')}")
        else:
            count = len(body.get("data", []))
            print(f"{entity:15s} OK       - returned {count} record(s)")
    except Exception as exc:
        print(f"{entity:15s} FAILED   - {exc}")
