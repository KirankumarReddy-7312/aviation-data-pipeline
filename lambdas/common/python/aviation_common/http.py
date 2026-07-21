"""
Minimal retrying HTTP JSON client built on urllib.request only.

Deliberately avoids the `requests` library: AWS Lambda's Python runtime
does not bundle it, and pulling it in would require shipping a Lambda
Layer for something urllib already does well enough for a handful of
GET calls every few minutes.
"""
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Callable, List, Optional


class ApiError(Exception):
    """Raised when an API call exhausts its retry budget."""


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_json(
    url: str,
    timeout: float = 30.0,
    max_retries: int = 4,
    backoff_base: float = 1.5,
) -> Any:
    """
    GET a URL and parse the JSON body, retrying on rate limiting and
    transient server errors with exponential backoff + jitter.

    Non-retryable errors (4xx other than 429, malformed JSON) raise
    immediately -- retrying a 401 or a bad request never helps.
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "aviation-pipeline/1.0"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)

        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_STATUS_CODES or attempt == max_retries:
                raise ApiError(f"HTTP {exc.code} calling {_redact(url)}: {exc.reason}") from exc

        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt == max_retries:
                raise ApiError(f"Network error calling {_redact(url)}: {exc}") from exc

        except json.JSONDecodeError as exc:
            raise ApiError(f"Malformed JSON from {_redact(url)}: {exc}") from exc

        sleep_seconds = (backoff_base ** attempt) + random.uniform(0, 0.5)
        time.sleep(sleep_seconds)

    raise ApiError(f"Exhausted retries calling {_redact(url)}: {last_error}")


def fetch_paginated(
    build_url: Callable[[int, int], str],
    page_size: int = 100,
    max_pages: int = 5,
    seconds_remaining_fn: Optional[Callable[[], float]] = None,
    min_seconds_remaining: float = 20.0,
) -> List[dict]:
    """
    Walk AviationStack's offset/limit pagination until either the
    endpoint runs out of data, max_pages is hit, or (if a Lambda time
    budget callback is supplied) the invocation is about to time out.

    AviationStack responses look like:
        {"pagination": {"limit": 100, "offset": 0, "count": 100, "total": 483500}, "data": [...]}

    max_pages exists so a single "flights" run can't try to pull
    hundreds of thousands of records and blow the Lambda timeout --
    tune it per entity via Terraform env vars.
    """
    records: List[dict] = []
    offset = 0

    for _ in range(max_pages):
        if seconds_remaining_fn is not None and seconds_remaining_fn() < min_seconds_remaining:
            break

        payload = fetch_json(build_url(offset, page_size))
        page_data = payload.get("data") or []
        records.extend(page_data)

        total = (payload.get("pagination") or {}).get("total", len(records))
        offset += page_size
        if not page_data or offset >= total:
            break

    return records


def _redact(url: str) -> str:
    """Strip the access_key query param before it ever hits a log line."""
    if "access_key=" not in url:
        return url
    parts = url.split("access_key=", 1)
    tail = parts[1].split("&", 1)
    remainder = f"&{tail[1]}" if len(tail) > 1 else ""
    return f"{parts[0]}access_key=***REDACTED***{remainder}"
