import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from aviation_common.http import ApiError, fetch_json, fetch_paginated


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_fetch_json_returns_parsed_body():
    with patch("aviation_common.http.urllib.request.urlopen", return_value=_FakeResponse({"ok": True})):
        assert fetch_json("https://example.com") == {"ok": True}


def test_fetch_json_raises_immediately_on_non_retryable_4xx():
    error = urllib.error.HTTPError("https://example.com", 401, "Unauthorized", {}, io.BytesIO(b""))
    with patch("aviation_common.http.urllib.request.urlopen", side_effect=error):
        with pytest.raises(ApiError):
            fetch_json("https://example.com", max_retries=3)


def test_fetch_json_retries_429_then_succeeds():
    error = urllib.error.HTTPError("https://example.com", 429, "Too Many Requests", {}, io.BytesIO(b""))
    responses = [error, error, _FakeResponse({"ok": True})]

    def side_effect(*args, **kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch("aviation_common.http.urllib.request.urlopen", side_effect=side_effect), \
         patch("aviation_common.http.time.sleep"):
        assert fetch_json("https://example.com", max_retries=3) == {"ok": True}


def test_fetch_json_gives_up_after_max_retries():
    error = urllib.error.HTTPError("https://example.com", 503, "Service Unavailable", {}, io.BytesIO(b""))
    with patch("aviation_common.http.urllib.request.urlopen", side_effect=error), \
         patch("aviation_common.http.time.sleep"):
        with pytest.raises(ApiError):
            fetch_json("https://example.com", max_retries=2)


def test_fetch_paginated_stops_when_offset_reaches_total():
    pages = [
        {"pagination": {"total": 150}, "data": [{"id": i} for i in range(100)]},
        {"pagination": {"total": 150}, "data": [{"id": i} for i in range(100, 150)]},
    ]

    def fake_fetch_json(url):
        return pages.pop(0)

    with patch("aviation_common.http.fetch_json", side_effect=fake_fetch_json):
        records = fetch_paginated(lambda offset, limit: f"url?offset={offset}", page_size=100, max_pages=10)

    assert len(records) == 150


def test_fetch_paginated_respects_max_pages_cap():
    def fake_fetch_json(url):
        return {"pagination": {"total": 100000}, "data": [{"id": 1}] * 100}

    with patch("aviation_common.http.fetch_json", side_effect=fake_fetch_json):
        records = fetch_paginated(lambda offset, limit: "url", page_size=100, max_pages=3)

    assert len(records) == 300


def test_fetch_paginated_stops_when_time_budget_exhausted():
    remaining = [30.0, 30.0, 5.0]  # third call is below min_seconds_remaining

    def fake_seconds_remaining():
        return remaining.pop(0)

    def fake_fetch_json(url):
        return {"pagination": {"total": 100000}, "data": [{"id": 1}] * 100}

    with patch("aviation_common.http.fetch_json", side_effect=fake_fetch_json):
        records = fetch_paginated(
            lambda offset, limit: "url",
            page_size=100,
            max_pages=10,
            seconds_remaining_fn=fake_seconds_remaining,
            min_seconds_remaining=20.0,
        )

    assert len(records) == 200  # only the first two pages ran before the budget check tripped
