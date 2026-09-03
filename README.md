# EdgeDash

EdgeDash is an autonomous career intelligence agent that runs on a schedule. Each
cycle it fetches live job listings from configured sources, scores every listing
for fit against your profile, identifies the skills appearing in jobs you cannot
yet land, verifies its own output for consistency, and publishes the results to a
Streamlit dashboard — all without manual input.

---

## Architecture

```
Trigger (scheduled)
        |
        v
   Orchestrator          reads state, delegates, never does work itself
    /    |    \
Fetcher Scorer GapAnalyzer   one goal each, one stop condition each
        |
        v
     Verifier            checks output before it is committed
        |
        v
      Storage            single module — the only place that touches the DB
        |
        v
   Dashboard (read-only) never writes
```

---

## Current status

### Week 1 — complete

- [x] `edgedash/config.py` — `Config` dataclass, loads from `config.yaml`
- [x] `edgedash/storage.py` — sole DB interface, all CRUD, dedup via stable hash IDs
- [x] `edgedash/agents/base.py` — `Agent` protocol, `AgentResult` dataclass
- [x] `edgedash/agents/mock_fetcher.py` — **temporary, kept for offline dev** (`use_mock_fetcher: true` in config)
- [x] `edgedash/orchestrator.py` — `run_cycle()`, registry pattern, cycle logging
- [x] `run_cycle.py` — entry point

### Week 2 — complete

- [x] `edgedash/sources/base.py` — `Source` protocol + `@register_source` decorator registry
- [x] `edgedash/sources/http.py` — single shared HTTP helper: 10s timeout, 2 retries, backoff, User-Agent
- [x] `edgedash/sources/arbeitnow.py` — Arbeitnow source, no API key required, city-filter relaxation
- [x] `edgedash/sources/apify.py` — Apify source, skips gracefully if `APIFY_TOKEN` absent
- [x] `edgedash/agents/fetcher.py` — real Fetcher, iterates source registry, per-source failure isolation
- [x] `edgedash/settings.py` — single env-var loader, reads `.env` via python-dotenv
- [x] `.env.example` — lists all required env vars with empty values (committed)
- [ ] `edgedash/agents/scorer.py` — LLM-based fit scoring, writes `fit_score` + `fit_reason`

### Week 3 — coming

- [ ] `edgedash/agents/gap_analyzer.py` — surfaces skill gaps from unmatched listings
- [ ] `edgedash/verifier.py` — sanity-checks scores and gaps before storage commit

### Week 4 — coming

- [ ] Migrate `edgedash/storage.py` from SQLite to hosted Postgres (one-file change by design)
- [ ] `dashboard/app.py` — Streamlit dashboard, read-only view of listings and gaps
- [ ] Scheduler / cron wiring for fully autonomous daily runs

---

## Setup

Requires Python 3.11 or later.

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd edgedash

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt
```

### Secrets

Copy `.env.example` to `.env` and fill in any keys you have:

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux
```

`.env` is gitignored. Never commit it. `APIFY_TOKEN` is optional — if absent, the Apify source skips itself cleanly.

### Configure

Edit `config.yaml` at the repo root before the first run:

```yaml
target_role: "Data Analyst"       # the role title you are targeting
target_city: "Bengaluru"          # city used to filter listings
keywords:                         # terms used to match and filter
  - "SQL"
  - "Python"
  - "Power BI"
my_skills:                        # your current skills, used for gap analysis
  - "Python"
  - "SQL"
  - "Excel"
experience_years: 2               # used by the scorer
db_path: "edgedash.db"            # SQLite file path (week 4: replace with DSN)
min_fit_score: 60                 # listings below this score are filtered in the dashboard
sources:                          # active job-board sources
  - "arbeitnow"                   # free, no key needed
  # - "apify"                     # uncomment after adding APIFY_TOKEN to .env
use_mock_fetcher: false           # set true to run offline without network calls
```

### Run

```bash
python run_cycle.py
```

The console prints the state read from the database, the plan the orchestrator chose, each agent's result, and a cycle summary. Every run is logged to the `cycle_log` table.

---

## Design decisions

**Storage is isolated behind one module.**
When the project moves from SQLite to hosted Postgres in week 4, only
`edgedash/storage.py` changes. No other module imports `sqlite3`, so the swap
cannot silently break callers in other files.

**Listing IDs are stable hashes of source + URL.**
The same job posted on two different days produces the same ID. `INSERT OR IGNORE`
on that primary key means a listing is stored exactly once no matter how many
times the fetcher runs, making deduplication observable and count-based metrics
trustworthy.

**The Orchestrator delegates instead of doing work itself.**
Keeping the orchestrator free of fetch and scoring logic means each concern is
tested in isolation. Adding or replacing an agent is a one-line registry change
with no risk of breaking the coordination logic.
