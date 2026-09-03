"""
Shared HTTP helper — the ONLY place in the project that calls requests.get.
Enforces: 10 s timeout, 2 retries with exponential backoff, real User-Agent.
Raises SourceError on unrecoverable failure (steering rule 11).
"""

from __future__ import annotations

import time
from typing import Any

import requests

_USER_AGENT = (
    "EdgeDash/1.0 (autonomous career intelligence agent; "
    "github.com/your-handle/edgedash)"
)
_TIMEOUT = 10          # seconds
_MAX_RETRIES = 2
_BACKOFF_BASE = 2.0    # seconds; doubles each retry


class SourceError(Exception):
    """Raised when an HTTP source fails after all retries."""


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """
    GET `url`, return parsed JSON.
    Retries up to _MAX_RETRIES times with exponential backoff.
    Raises SourceError if every attempt fails.
    """
    merged_headers = {"User-Agent": _USER_AGENT}
    if headers:
        merged_headers.update(headers)

    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=merged_headers,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE ** attempt
                time.sleep(wait)

    raise SourceError(
        f"GET {url} failed after {_MAX_RETRIES + 1} attempts: {last_exc}"
    )
