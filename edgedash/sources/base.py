"""
Source protocol and registry.
Every source class registers itself with @register_source.
The Fetcher iterates SOURCES — it never imports a source directly.
"""

from __future__ import annotations

from typing import Protocol

from edgedash.config import Config


class Source(Protocol):
    """
    Uniform interface every job-board source must satisfy.

    Returns normalised dicts with EXACTLY these keys (steering rule 10):
        source, external_id, title, company, location, url,
        description, posted_at, raw
    Missing values must be None — not empty string, not "N/A".
    """

    name: str

    def fetch(self, config: Config) -> list[dict]:
        ...


# Registry: maps source name -> class
SOURCES: dict[str, type] = {}


def register_source(cls: type) -> type:
    """Class decorator — adds the source to SOURCES under cls.name."""
    SOURCES[cls.name] = cls
    return cls
