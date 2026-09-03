"""
Arbeitnow source — free public job board API, no API key required.
API docs: https://www.arbeitnow.com/api/job-board-api

Paging strategy: fetch up to 5 pages, stop early if a page returns
zero keyword matches (no point fetching further).

Filtering: match config.keywords against title + description (case-insensitive).
If strict city filtering would leave fewer than 5 results, relax location
and log that the filter was relaxed (steering rule 12 spirit: prefer results
over empty database).
"""

from __future__ import annotations

import time

from edgedash.config import Config
from edgedash.sources.base import register_source
from edgedash.sources.http import SourceError, get_json

_API_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 5
_MIN_RESULTS_BEFORE_RELAX = 5
_RATE_LIMIT_SECONDS = 1.0  # steering rule 14: max 1 req/s


def _matches_keywords(job: dict, keywords: list[str]) -> bool:
    text = (
        (job.get("title") or "") + " " + (job.get("description") or "")
    ).lower()
    return any(kw.lower() in text for kw in keywords)


def _matches_city(job: dict, city: str) -> bool:
    location = (job.get("location") or "").lower()
    return city.lower() in location


def _normalise(job: dict) -> dict:
    return {
        "source":      "arbeitnow",
        "external_id": job.get("slug"),          # stable slug, not a hash
        "title":       job.get("title") or None,
        "company":     job.get("company_name") or None,
        "location":    job.get("location") or None,
        "url":         job.get("url") or None,
        "description": job.get("description") or None,
        "posted_at":   job.get("created_at") or None,
        "raw":         job,
    }


@register_source
class ArbeitnowSource:
    name: str = "arbeitnow"

    def fetch(self, config: Config) -> list[dict]:
        raw_hits: list[dict] = []

        for page in range(1, _MAX_PAGES + 1):
            try:
                data = get_json(_API_URL, params={"page": page})
            except SourceError as exc:
                print(f"  [arbeitnow] page {page} failed: {exc}")
                break

            jobs: list[dict] = data.get("data", [])
            if not jobs:
                break

            matched = [j for j in jobs if _matches_keywords(j, config.keywords)]
            print(
                f"  [arbeitnow] page {page}: "
                f"{len(jobs)} raw, {len(matched)} keyword-matched"
            )
            raw_hits.extend(matched)

            # Stop paging if this page had zero keyword matches
            if not matched:
                break

            if page < _MAX_PAGES:
                time.sleep(_RATE_LIMIT_SECONDS)

        # Strict city filter
        city_filtered = [j for j in raw_hits if _matches_city(j, config.target_city)]

        if len(city_filtered) < _MIN_RESULTS_BEFORE_RELAX:
            print(
                f"  [arbeitnow] city filter '{config.target_city}' left only "
                f"{len(city_filtered)} result(s) — relaxing to all locations "
                f"({len(raw_hits)} results kept)"
            )
            final = raw_hits
        else:
            final = city_filtered

        print(
            f"  [arbeitnow] {len(raw_hits)} keyword-matched → "
            f"{len(final)} after location filter"
        )

        return [_normalise(j) for j in final]
