"""State inspection — deterministic, testable, no LLM, arithmetic on timestamps and counts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from edgedash import storage
from edgedash.config import Config


@dataclass
class SystemState:
    """Snapshot of system state for planning decisions."""
    last_fetch_at: str | None
    hours_since_fetch: float | None
    unscored_count: int
    gaps_computed_at: str | None
    gaps_stale: bool
    last_cycle_verdict: str | None
    last_cycle_at: str | None


def read_state(config: Config, now: datetime) -> SystemState:
    """
    Read system state from storage. Pure function of (config, now) — testable.
    Uses only cheap queries: counts and max(timestamp), no full table loads.
    """
    # Fetch timing
    last_fetch_at = storage.last_fetch_time(config.db_path)
    hours_since_fetch: float | None = None
    if last_fetch_at:
        try:
            last_fetch_dt = datetime.fromisoformat(last_fetch_at)
            hours_since_fetch = (now - last_fetch_dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            hours_since_fetch = None

    # Unscored count
    unscored_count = storage.count_unscored(config.db_path)

    # Gap analysis timing and staleness
    gaps_computed_at = storage.last_gap_snapshot_time(config.db_path)
    gaps_stale = False
    if gaps_computed_at:
        # Gaps are stale if any score is newer than the gap snapshot
        last_scored_at = storage.last_scored_time(config.db_path)
        if last_scored_at:
            try:
                gap_dt = datetime.fromisoformat(gaps_computed_at)
                score_dt = datetime.fromisoformat(last_scored_at)
                gaps_stale = score_dt > gap_dt
            except (ValueError, TypeError):
                gaps_stale = True  # conservative: treat as stale if parsing fails
        else:
            # Gap snapshot exists but no scored listings yet → stale (snapshot is meaningless)
            gaps_stale = True
    else:
        # No gap snapshot exists → treat as stale (needs analysis)
        gaps_stale = True

    # Last cycle info
    last_cycle_at = storage.last_cycle_time(config.db_path)
    last_cycle_verdict = storage.last_cycle_status(config.db_path)

    return SystemState(
        last_fetch_at=last_fetch_at,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=last_cycle_verdict,
        last_cycle_at=last_cycle_at,
    )
