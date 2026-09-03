"""Quick smoke-test for ArbeitnowSource — no database touched."""

from edgedash.config import load_config
from edgedash.sources.arbeitnow import ArbeitnowSource

config = load_config()
source = ArbeitnowSource()
results = source.fetch(config)

print(f"\nTotal results: {len(results)}")
for r in results[:3]:
    print(f"  [{r['source']}] {r['title']} @ {r['company']} — {r['location']}")
    print(f"    id={r['external_id']}  url={r['url']}")
