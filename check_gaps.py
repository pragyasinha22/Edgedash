"""Quick check of gaps_stale logic."""

from edgedash import storage
from edgedash.config import load_config

config = load_config()
gaps_computed_at = storage.last_gap_snapshot_time(config.db_path)
last_scored_at = storage.last_scored_time(config.db_path)

print(f"gaps_computed_at: {gaps_computed_at!r} (type: {type(gaps_computed_at).__name__})")
print(f"last_scored_at: {last_scored_at!r} (type: {type(last_scored_at).__name__})")

if gaps_computed_at and last_scored_at:
    from datetime import datetime
    gap_dt = datetime.fromisoformat(gaps_computed_at)
    score_dt = datetime.fromisoformat(last_scored_at)
    print(f"gap_dt: {gap_dt}")
    print(f"score_dt: {score_dt}")
    print(f"score_dt > gap_dt: {score_dt > gap_dt}")
