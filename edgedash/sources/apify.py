"""
Apify source — scrapes job listings via Apify's job-search actor.
Requires APIFY_TOKEN in the environment (.env file).
Per steering rule 13: if the token is absent, logs and returns [] without crashing.
Per steering rule 11: all HTTP goes through get_json — no bare requests.get here.
Results are capped at 100 to protect free-tier credits.
"""

from __future__ import annotations

import logging

from edgedash import settings
from edgedash.config import Config
from edgedash.sources.base import register_source
from edgedash.sources.http import SourceError, get_json

logger = logging.getLogger(__name__)

# Apify run-sync-get-dataset-items endpoint.
# Actor: apify/indeed-scraper is free-tier compatible and returns job listings.
_ACTOR_ID = "hMvNSpz3JnHgl5jkh"   # Apify "Job Search" actor (no signup scraper)
_BASE_URL  = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
_CAP       = 100


def _endpoint(actor_id: str) -> str:
    return _BASE_URL.format(actor=actor_id)


def _normalise(item: dict) -> dict:
    """Map Apify actor output fields onto our schema (steering rule 10)."""
    return {
        "source":      "apify",
        "external_id": item.get("id") or item.get("jobId") or item.get("externalId") or None,
        "title":       item.get("title") or item.get("positionName") or None,
        "company":     item.get("company") or item.get("companyName") or None,
        "location":    item.get("location") or item.get("jobLocation") or None,
        "url":         item.get("url") or item.get("jobUrl") or None,
        "description": item.get("description") or item.get("jobDescription") or None,
        "posted_at":   item.get("postedAt") or item.get("datePosted") or None,
        "raw":         item,
    }


@register_source
class ApifySource:
    name: str = "apify"

    def fetch(self, config: Config) -> list[dict]:
        token = settings.get("APIFY_TOKEN")
        if not token:
            logger.info("apify: no APIFY_TOKEN in environment — skipping source")
            return []

        params = {
            "token":   token,
            "position": config.target_role,
            "location": config.target_city,
            "maxItems": _CAP,
        }

        try:
            data = get_json(_endpoint(_ACTOR_ID), params=params)
        except SourceError as exc:
            logger.warning(f"apify: fetch failed: {exc}")
            return []

        # Actor may return a list directly or a dict with a "items" key
        items: list[dict] = data if isinstance(data, list) else data.get("items", [])
        items = items[:_CAP]  # hard cap — never exceed free tier limit

        logger.info(f"apify: {len(items)} results returned")
        return [_normalise(item) for item in items]
