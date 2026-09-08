"""Debug script to check gaps_stale logic without running full cycle."""

from datetime import datetime, timezone
from edgedash.config import load_config
from edgedash.state import read_state

config = load_config()
now = datetime.now(timezone.utc)
state = read_state(config, now)

print("\n=== gaps_stale debug ===")
print(f"gaps_computed_at: {state.gaps_computed_at}")
print(f"gaps_stale: {state.gaps_stale}")
