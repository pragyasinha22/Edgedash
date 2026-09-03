"""Debug script — inspect what Arbeitnow actually returns and how filters behave."""

from edgedash.sources.http import get_json
from edgedash.sources.arbeitnow import _matches_role, _location_tier

data = get_json("https://www.arbeitnow.com/api/job-board-api", params={"page": 1})
jobs = data.get("data", [])
matched = [j for j in jobs if _matches_role(j)]

print(f"Total jobs: {len(jobs)}, role-matched: {len(matched)}")
print()

print("=== ROLE-MATCHED JOBS (first 10) ===")
for j in matched[:10]:
    tier = _location_tier(j)
    print(f"  title    : {j.get('title')}")
    print(f"  location : {j.get('location')}  (tier={tier})")
    print()

# Also show a sample of non-matched titles so we can see what's being rejected
not_matched = [j for j in jobs if not _matches_role(j)]
print(f"=== SAMPLE NOT-MATCHED TITLES (first 10 of {len(not_matched)}) ===")
for j in not_matched[:10]:
    print(f"  {j.get('title')} | {j.get('location')}")
