from decimal import Decimal

from aviation_common.config import ENTITY_CONFIG, DEFAULT_FETCH_ENTITIES, to_decimal


def test_flights_extract_flattens_nested_json():
    raw = {
        "flight_date": "2026-02-11",
        "flight_status": "scheduled",
        "departure": {"iata": "PEK"},
        "arrival": {"iata": "LZY"},
        "airline": {"name": "Air China LTD", "iata": "CA"},
        "flight": {"number": "4127", "iata": "CA4127"},
        "aircraft": {"icao24": "780356"},
    }
    row = ENTITY_CONFIG["flights"]["extract"](raw)
    assert row == {
        "flight_iata": "CA4127",
        "flight_date": "2026-02-11",
        "airline_iata": "CA",
        "airline_name": "Air China LTD",
        "departure_iata": "PEK",
        "arrival_iata": "LZY",
        "flight_status": "scheduled",
        "aircraft_icao24": "780356",
    }


def test_flights_extract_tolerates_missing_aircraft():
    raw = {
        "flight_date": "2026-02-11",
        "flight_status": "scheduled",
        "departure": {"iata": "PEK"},
        "arrival": {"iata": "LZY"},
        "airline": {"name": "Air China LTD", "iata": "CA"},
        "flight": {"iata": "CA4127"},
        "aircraft": None,
    }
    row = ENTITY_CONFIG["flights"]["extract"](raw)
    assert row["aircraft_icao24"] is None


def test_flights_key_fn_builds_gsi1_keys():
    row = {"flight_iata": "CA4127", "flight_date": "2026-02-11", "airline_iata": "CA"}
    keys = ENTITY_CONFIG["flights"]["key_fn"](row)
    assert keys == {
        "PK": "FLIGHT#CA4127",
        "SK": "DATE#2026-02-11",
        "GSI1PK": "AIRLINE#CA",
        "GSI1SK": "DATE#2026-02-11",
    }


def test_flights_key_fn_omits_gsi_keys_without_airline():
    row = {"flight_iata": "CA4127", "flight_date": "2026-02-11", "airline_iata": ""}
    keys = ENTITY_CONFIG["flights"]["key_fn"](row)
    assert "GSI1PK" not in keys
    assert "GSI1SK" not in keys


def test_airports_metadata_key_fn():
    keys = ENTITY_CONFIG["airports"]["key_fn"]({"iata_code": "DEL"})
    assert keys == {"PK": "AIRPORT#DEL", "SK": "METADATA"}


def test_countries_key_fn():
    keys = ENTITY_CONFIG["countries"]["key_fn"]({"country_iso2": "IN"})
    assert keys == {"PK": "COUNTRY#IN", "SK": "METADATA"}


def test_every_entity_has_consistent_shape():
    for name, cfg in ENTITY_CONFIG.items():
        assert cfg["endpoint"], name
        assert cfg["columns"], name
        assert callable(cfg["extract"]), name
        assert callable(cfg["key_fn"]), name
        assert cfg["required"], name
        assert set(cfg["required"]).issubset(cfg["columns"]), name
        assert set(cfg["numeric_fields"]).issubset(cfg["columns"]), name
        assert cfg["refresh"] in {"hot", "reference"}, name
        assert isinstance(cfg["schema_verified"], bool), name


def test_default_fetch_entities_excludes_unverified_schemas():
    assert "routes" not in DEFAULT_FETCH_ENTITIES
    assert "airplanes" not in DEFAULT_FETCH_ENTITIES
    assert "flights" in DEFAULT_FETCH_ENTITIES
    assert "airports" in DEFAULT_FETCH_ENTITIES


def test_to_decimal_handles_valid_blank_and_junk_values():
    assert to_decimal("28.5665") == Decimal("28.5665")
    assert to_decimal("") is None
    assert to_decimal(None) is None
    assert to_decimal("not-a-number") is None
