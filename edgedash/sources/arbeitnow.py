"""
Arbeitnow source — free public job board API, no API key required.
API docs: https://www.arbeitnow.com/api/job-board-api

Paging strategy: fetch up to 5 pages, stop early if a page returns
zero role-signal matches (no point fetching further).

Filtering:
- ROLE filter: job must have a data-analyst role signal in title or description
  PLUS at least one technical keyword. Title-only strong signals also qualify.
- LOCATION filter: accept Bengaluru/Bangalore (normalised), Remote, Hybrid,
  and India as weaker match. Never silently fall back to worldwide results.
"""

from __future__ import annotations

import time

from edgedash.config import Config
from edgedash.sources.base import register_source
from edgedash.sources.http import SourceError, get_json

_API_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 5
_RATE_LIMIT_SECONDS = 1.0  # steering rule 14: max 1 req/s

# Role signals — these define what makes a *relevant* job
ROLE_SIGNALS = [
    "data analyst",
    "business analyst",
    "bi analyst",
    "business intelligence",
    "reporting analyst",
    "analytics analyst",
    "data specialist",
    "insights analyst",
]

# Technical keywords — boost relevance, required alongside a role signal
TECH_KEYWORDS = [
    "sql",
    "python",
    "power bi",
    "tableau",
    "excel",
    "pandas",
    "data visualization",
    "looker",
    "dbt",
]

# Bengaluru/Bangalore spelling variants to normalise
_BLR_VARIANTS = ["bengaluru", "bangalore", "bengaluru,", "bangalore,"]


def _normalise_location(location: str) -> str:
    """Lowercase and normalise Bengaluru/Bangalore spelling."""
    loc = location.lower().strip()
    # Replace both variants with canonical form for consistent matching
    loc = loc.replace("bangalore", "bengaluru")
    return loc


def _matches_role(job: dict) -> bool:
    """
    A job qualifies if:
      - A role signal appears in (title + description) AND at least one tech keyword, OR
      - The title alone contains a strong role signal (even without tech keywords).
    """
    title = (job.get("title") or "").lower()
    description = (job.get("description") or "").lower()
    full_text = title + " " + description

    has_role_signal = any(sig in full_text for sig in ROLE_SIGNALS)
    has_tech = any(kw in full_text for kw in TECH_KEYWORDS)
    title_has_role = any(sig in title for sig in ROLE_SIGNALS)

    # Title alone with a role signal qualifies (e.g. "Data Analyst – Risk")
    if title_has_role:
        return True
    # Role signal + tech keyword in full text qualifies
    if has_role_signal and has_tech:
        return True
    return False


def _location_tier(job: dict) -> int:
    """
    Return a tier score for location relevance (higher = better match).
      3 = Bengaluru/Bangalore exact match
      2 = Remote or Hybrid (with optional Bengaluru/Bangalore mention)
      1 = India (general)
      0 = no match (foreign city)
    """
    raw_location = (job.get("location") or "").lower()
    loc = _normalise_location(raw_location)

    # Tier 3: direct Bengaluru/Bangalore match
    if "bengaluru" in loc:
        return 3

    # Tier 2: remote or hybrid — acceptable regardless of explicit city
    if "remote" in loc or "hybrid" in loc:
        return 2

    # Tier 1: India-wide mention (weaker match but still relevant)
    if "india" in loc:
        return 1

    return 0


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
        role_matched: list[dict] = []

        for page in range(1, _MAX_PAGES + 1):
            try:
                data = get_json(_API_URL, params={"page": page})
            except SourceError as exc:
                print(f"  [arbeitnow] page {page} failed: {exc}")
                break

            jobs: list[dict] = data.get("data", [])
            if not jobs:
                break

            matched = [j for j in jobs if _matches_role(j)]
            print(
                f"  [arbeitnow] page {page}: "
                f"{len(jobs)} raw, {len(matched)} role-matched"
            )
            role_matched.extend(matched)

            # Stop paging early if this page had zero role matches
            if not matched:
                break

            if page < _MAX_PAGES:
                time.sleep(_RATE_LIMIT_SECONDS)

        # Location filtering with explicit tiering
        # Arbeitnow is a European board — Bengaluru/India results are rare.
        # Priority: tier3 (Bengaluru) > tier2 (Remote/Hybrid) > tier1 (India)
        # > tier0 (worldwide role-matched). We always return something useful
        # rather than an empty database, but we log exactly what tier was used.
        tier3 = [j for j in role_matched if _location_tier(j) == 3]
        tier2 = [j for j in role_matched if _location_tier(j) == 2]
        tier1 = [j for j in role_matched if _location_tier(j) == 1]
        tier0 = [j for j in role_matched if _location_tier(j) == 0]

        if tier3:
            final = tier3
            loc_note = f"Bengaluru/Bangalore: {len(tier3)}"
        elif tier2:
            final = tier2
            print(f"  [arbeitnow] No Bengaluru results — using Remote/Hybrid ({len(tier2)})")
            loc_note = f"Remote/Hybrid fallback: {len(tier2)}"
        elif tier1:
            final = tier1
            print(f"  [arbeitnow] No Bengaluru/Remote — using India-wide ({len(tier1)})")
            loc_note = f"India-wide fallback: {len(tier1)}"
        elif tier0:
            # Arbeitnow is European — role-matched jobs are still relevant for
            # skills/gap analysis even if location doesn't match. Keep them but
            # they will score low on location in the Scorer.
            final = tier0
            print(
                f"  [arbeitnow] No India/Remote results. Keeping {len(tier0)} "
                f"worldwide role-matched jobs for skills analysis "
                f"(will score low on location)."
            )
            loc_note = f"worldwide role-matched: {len(tier0)}"
        else:
            final = []
            loc_note = "no role-matched jobs found"

        print(
            f"  [arbeitnow] {len(role_matched)} role-matched → "
            f"{len(final)} after location filter ({loc_note})"
        )

        return [_normalise(j) for j in final]
