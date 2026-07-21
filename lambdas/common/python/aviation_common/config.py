"""
Single source of truth for every AviationStack entity this pipeline ingests.

Every Lambda (extract, transform, load) imports ENTITY_CONFIG instead of
re-declaring column lists / key patterns, so the three stages can never
drift out of sync with each other. The query API and Glue/Athena table
definitions also key off this file's column lists.

Entities beyond the original 6 (routes, airplanes) are marked
schema_verified=False: their extract() mapping is a best-effort guess at
AviationStack's real response shape, not something fetched and confirmed
against a live API key. They're fully wired end-to-end (Lambda 2/3 handle
them like any other entity) but Lambda 1 excludes them from its default
"fetch everything" set, so they only run once you explicitly pass them in
an event payload -- after you've run
`scripts/inspect_endpoint.py <entity>` against your own API key and fixed
up the extract() mapping below if the real field names differ. Bronze/raw
storage is schema-agnostic either way (the full API response is archived
verbatim regardless of what Silver/Serving extract), so a wrong mapping
here costs a Silver/DynamoDB backfill, not lost data.
"""
from decimal import Decimal
from typing import Any, Callable, Optional


def _get(record: dict, *path: str) -> Optional[Any]:
    """Safely walk a chain of nested dict keys, returning None if any hop is missing."""
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _flights_extract(record: dict) -> dict:
    return {
        "flight_iata": _get(record, "flight", "iata"),
        "flight_date": record.get("flight_date"),
        "airline_iata": _get(record, "airline", "iata"),
        "airline_name": _get(record, "airline", "name"),
        "departure_iata": _get(record, "departure", "iata"),
        "arrival_iata": _get(record, "arrival", "iata"),
        "flight_status": record.get("flight_status"),
        "aircraft_icao24": _get(record, "aircraft", "icao24"),
    }


def _flights_key_fn(row: dict) -> dict:
    keys = {
        "PK": f"FLIGHT#{row['flight_iata']}",
        "SK": f"DATE#{row['flight_date']}",
    }
    if row.get("airline_iata"):
        keys["GSI1PK"] = f"AIRLINE#{row['airline_iata']}"
        keys["GSI1SK"] = f"DATE#{row['flight_date']}"
    return keys


def _airports_extract(record: dict) -> dict:
    return {
        "iata_code": record.get("iata_code"),
        "airport_name": record.get("airport_name"),
        "country_name": record.get("country_name"),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "timezone": record.get("timezone"),
    }


def _metadata_key_fn(prefix: str) -> Callable[[dict], dict]:
    def key_fn(row: dict) -> dict:
        return {"PK": f"{prefix}#{row['iata_code']}", "SK": "METADATA"}
    return key_fn


def _airlines_extract(record: dict) -> dict:
    return {
        "iata_code": record.get("iata_code"),
        "airline_name": record.get("airline_name"),
        "country_name": record.get("country_name"),
        "fleet_size": record.get("fleet_size"),
        "status": record.get("status"),
    }


def _aircraft_types_extract(record: dict) -> dict:
    return {
        "iata_code": record.get("iata_code"),
        "aircraft_name": record.get("aircraft_name"),
    }


def _cities_extract(record: dict) -> dict:
    return {
        "iata_code": record.get("iata_code"),
        "city_name": record.get("city_name"),
        "country_iso2": record.get("country_iso2"),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "timezone": record.get("timezone"),
    }


def _countries_extract(record: dict) -> dict:
    return {
        "country_iso2": record.get("country_iso2"),
        "country_name": record.get("country_name"),
        "continent": record.get("continent"),
        "capital": record.get("capital"),
        "population": record.get("population"),
    }


def _countries_key_fn(row: dict) -> dict:
    return {"PK": f"COUNTRY#{row['country_iso2']}", "SK": "METADATA"}


def _routes_extract(record: dict) -> dict:
    # Best-effort mapping: AviationStack documents /v1/routes as sharing the
    # same nested airline/flight/departure/arrival shape as /v1/flights, just
    # without status/live fields. Verify with inspect_endpoint.py before relying on it.
    return {
        "flight_iata": _get(record, "flight", "iata"),
        "airline_iata": _get(record, "airline", "iata"),
        "airline_name": _get(record, "airline", "name"),
        "departure_iata": _get(record, "departure", "iata"),
        "arrival_iata": _get(record, "arrival", "iata"),
    }


def _routes_key_fn(row: dict) -> dict:
    return {"PK": f"ROUTE#{row['flight_iata']}", "SK": "METADATA"}


def _airplanes_extract(record: dict) -> dict:
    # Best-effort mapping: AviationStack's fleet/tail-number registry is
    # documented as a flat (non-nested) object keyed by registration_number.
    # Verify with inspect_endpoint.py before relying on it.
    return {
        "registration_number": record.get("registration_number"),
        "airline_iata": record.get("airline_iata"),
        "model_code": record.get("model_code"),
        "production_line": record.get("production_line"),
        "engines_count": record.get("engines_count"),
        "engines_type": record.get("engines_type"),
        "plane_status": record.get("plane_status"),
    }


def _airplanes_key_fn(row: dict) -> dict:
    return {"PK": f"AIRPLANE#{row['registration_number']}", "SK": "METADATA"}


# entity name -> config
#   endpoint         AviationStack API path segment
#   columns          ordered CSV column list (Silver layer schema)
#   extract          raw JSON record -> flat dict (used by Lambda 2)
#   key_fn           CSV row (dict of strings) -> {"PK": ..., "SK": ..., ["GSI1PK"/"GSI1SK"]} (used by Lambda 3)
#   required         columns that must be non-empty or the record/row is dropped
#   numeric_fields   columns that should be stored as Decimal in DynamoDB, not string
#   refresh          "hot" (fetched every 10 min) or "reference" (fetched daily) -- drives
#                    which EventBridge schedule invokes Lambda 1 for this entity
#   schema_verified  False means extract() is a best-effort guess, not confirmed against a
#                    live response -- excluded from Lambda 1's default entity set until verified
ENTITY_CONFIG = {
    "flights": {
        "endpoint": "flights",
        "columns": [
            "flight_iata", "flight_date", "airline_iata", "airline_name",
            "departure_iata", "arrival_iata", "flight_status", "aircraft_icao24",
        ],
        "extract": _flights_extract,
        "key_fn": _flights_key_fn,
        "required": ["flight_iata", "flight_date"],
        "numeric_fields": [],
        "refresh": "hot",
        "schema_verified": True,
    },
    "airports": {
        "endpoint": "airports",
        "columns": ["iata_code", "airport_name", "country_name", "latitude", "longitude", "timezone"],
        "extract": _airports_extract,
        "key_fn": _metadata_key_fn("AIRPORT"),
        "required": ["iata_code"],
        "numeric_fields": ["latitude", "longitude"],
        "refresh": "reference",
        "schema_verified": True,
    },
    "airlines": {
        "endpoint": "airlines",
        "columns": ["iata_code", "airline_name", "country_name", "fleet_size", "status"],
        "extract": _airlines_extract,
        "key_fn": _metadata_key_fn("AIRLINE"),
        "required": ["iata_code"],
        "numeric_fields": ["fleet_size"],
        "refresh": "reference",
        "schema_verified": True,
    },
    "aircraft_types": {
        "endpoint": "aircraft_types",
        "columns": ["iata_code", "aircraft_name"],
        "extract": _aircraft_types_extract,
        "key_fn": _metadata_key_fn("AIRCRAFT"),
        "required": ["iata_code"],
        "numeric_fields": [],
        "refresh": "reference",
        "schema_verified": True,
    },
    "cities": {
        "endpoint": "cities",
        "columns": ["iata_code", "city_name", "country_iso2", "latitude", "longitude", "timezone"],
        "extract": _cities_extract,
        "key_fn": _metadata_key_fn("CITY"),
        "required": ["iata_code"],
        "numeric_fields": ["latitude", "longitude"],
        "refresh": "reference",
        "schema_verified": True,
    },
    "countries": {
        "endpoint": "countries",
        "columns": ["country_iso2", "country_name", "continent", "capital", "population"],
        "extract": _countries_extract,
        "key_fn": _countries_key_fn,
        "required": ["country_iso2"],
        "numeric_fields": ["population"],
        "refresh": "reference",
        "schema_verified": True,
    },
    "routes": {
        "endpoint": "routes",
        "columns": ["flight_iata", "airline_iata", "airline_name", "departure_iata", "arrival_iata"],
        "extract": _routes_extract,
        "key_fn": _routes_key_fn,
        "required": ["flight_iata"],
        "numeric_fields": [],
        "refresh": "reference",
        "schema_verified": False,
    },
    "airplanes": {
        "endpoint": "airplanes",
        "columns": [
            "registration_number", "airline_iata", "model_code",
            "production_line", "engines_count", "engines_type", "plane_status",
        ],
        "extract": _airplanes_extract,
        "key_fn": _airplanes_key_fn,
        "required": ["registration_number"],
        "numeric_fields": ["engines_count"],
        "refresh": "reference",
        "schema_verified": False,
    },
}

ALL_ENTITIES = list(ENTITY_CONFIG.keys())
HOT_ENTITIES = [name for name, cfg in ENTITY_CONFIG.items() if cfg["refresh"] == "hot"]
REFERENCE_ENTITIES = [name for name, cfg in ENTITY_CONFIG.items() if cfg["refresh"] == "reference"]
# What Lambda 1 fetches when an event doesn't specify "entities" -- excludes
# unverified schemas so a bad guess can't silently start writing junk rows.
DEFAULT_FETCH_ENTITIES = [name for name, cfg in ENTITY_CONFIG.items() if cfg["schema_verified"]]


def to_decimal(value: Any) -> Optional[Decimal]:
    """Convert a CSV string / float to Decimal for DynamoDB, tolerating blanks and junk."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
