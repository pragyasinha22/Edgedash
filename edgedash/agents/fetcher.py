"""
Real Fetcher agent — iterates enabled sources, combines rows, writes to storage.
Source-specific logic lives in each Source class; this agent knows nothing about
individual job boards (steering rule 9).
"""

from __future__ import annotations

from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.agents.registry import register_agent
from edgedash.config import Config
from edgedash.sources import arbeitnow as _  # ensure sources register themselves  # noqa: F401
from edgedash.sources import apify as _apify  # noqa: F401
from edgedash.sources.base import SOURCES

NAME = "Fetcher"


def _run_source(source_name: str, config: Config, max_pages: int | None = None) -> tuple[list[dict], str]:
    """
    Run one source and return (rows, summary_fragment).
    Never raises — failures are caught and reported as a fragment string.
    """
    if source_name not in SOURCES:
        return [], f"{source_name}: UNKNOWN SOURCE"

    source = SOURCES[source_name]()
    try:
        rows = source.fetch(config)
        # Respect max_pages if provided (truncates rows from the source)
        if max_pages is not None and max_pages > 0:
            rows = rows[:max_pages]
        return rows, None   # summary built after upsert so we know new count
    except Exception as exc:  # steering rule 12: one dead source must not kill cycle
        fragment = f"{source_name}: FAILED ({type(exc).__name__}: {exc})"
        return [], fragment


@register_agent
class Fetcher:
    name: str = NAME

    def __init__(self, config: Config | None = None) -> None:
        # Config needed for registry compatibility, but not used in Fetcher
        pass

    def run(self, config: Config, stop_conditions: dict | None = None) -> AgentResult:
        started = datetime.now(timezone.utc).isoformat()

        stop = stop_conditions or {}
        max_listings = stop.get("max_listings")
        max_pages = stop.get("max_pages")

        all_rows: list[dict] = []
        fragments: list[str] = []

        for source_name in config.sources:
            rows, failure = _run_source(source_name, config, max_pages)

            if failure:
                print(f"  [fetcher] WARNING: {failure}")
                storage.log_cycle(
                    path=config.db_path,
                    agent=f"Fetcher/{source_name}",
                    started_at=started,
                    records_touched=0,
                    status="failed",
                    notes=failure,
                )
                fragments.append(failure)
            else:
                all_rows.extend(rows)
                # Respect max_listings across all sources
                if max_listings is not None and len(all_rows) >= max_listings:
                    all_rows = all_rows[:max_listings]
                    break

        # Compute stable IDs using the same function storage already uses
        for row in all_rows:
            if not row.get("id"):
                row["id"] = storage.make_listing_id(
                    row["source"], row["url"] or ""
                )

        # Normalise keys: external_id -> stored as part of the row payload
        # storage.upsert_listings expects our DB schema keys
        db_rows = [_to_db_row(r) for r in all_rows]
        new_count = storage.upsert_listings(config.db_path, db_rows)

        # Build per-source success fragments now that we have the new count
        for source_name in config.sources:
            source_rows = [r for r in all_rows if r.get("source") == source_name]
            if source_rows:
                # Approximate new count per source proportionally
                fragments.append(f"{source_name}: {len(source_rows)} rows ({new_count} new total)")

        notes = " | ".join(fragments) if fragments else f"0 rows from all sources"

        storage.log_cycle(
            path=config.db_path,
            agent=NAME,
            started_at=started,
            records_touched=new_count,
            status="ok",
            notes=notes,
        )

        return AgentResult(
            agent=NAME,
            status="ok",
            records_touched=new_count,
            notes=notes,
        )


def _to_db_row(row: dict) -> dict:
    """Map normalised source keys to the listings table schema."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id":          row.get("id"),
        "title":       row.get("title"),
        "company":     row.get("company"),
        "location":    row.get("location"),
        "url":         row.get("url"),
        "description": row.get("description"),
        "source":      row.get("source"),
        "posted_at":   row.get("posted_at"),
        # Always stamp fetched_at with current time for new rows
        "fetched_at":  row.get("fetched_at") or now,
        "fit_score":   row.get("fit_score"),
        "fit_reason":  row.get("fit_reason"),
    }
