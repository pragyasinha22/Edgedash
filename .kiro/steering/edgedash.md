# EdgeDash — Project Steering Rules

## Project Overview

EdgeDash is an autonomous AI career intelligence agent. It runs as a scheduled loop that fetches live job listings, scores them for fit against a user profile, surfaces skill gaps, verifies its own output, and publishes a Streamlit dashboard.

---

## Architecture

Do not deviate from this architecture without explicitly telling the user first.

```
Trigger (scheduled)
  -> Orchestrator
    -> Fetcher        (sub-agent)
    -> Scorer         (sub-agent)
    -> GapAnalyzer    (sub-agent)
  -> Verifier
  -> Storage
  -> Dashboard        (read-only)
```

- The **Orchestrator** reads state and delegates work to sub-agents. It never fetches job data or scores listings directly.
- Each **sub-agent** has exactly one goal and one stop condition.
- The **Dashboard** is read-only. It never writes to storage.

---

## Hard Rules

1. **Python 3.11+. Standard library first.**
   Add a third-party dependency only when it genuinely saves real work. State the reason before adding it.

2. **All storage access goes through a single `storage` module with a thin interface.**
   No other module may import `sqlite3` directly. The backend will switch from SQLite to hosted Postgres in week 4, and that must be a one-file change.

3. **Never hardcode user-specific values.**
   Role, city, keywords, and skills profile all live in config (e.g., `config.yaml` or a config module). No literals for these in any other file.

4. **No secrets in code.**
   All secrets and credentials are loaded from environment variables, in one place only (e.g., a dedicated `settings` or `env` module).

5. **Every agent run writes a row to `cycle_log`.**
   Required fields: what ran, timestamp, records touched, pass/fail status, and retry reason (if any).

6. **Fail loudly.**
   No bare `except: pass` or silent swallowing of errors. If something is wrong, it must surface visibly.

7. **Type hints on every function signature.**
   Docstrings only where the intent is not obvious from the function name.

8. **Keep files under ~150 lines.**
   Split a module before it becomes a problem, not after.

---

## Style

- Small, testable functions over large procedural blocks.
- Plain, readable Python over clever Python.
- When asked to build one module, build one module — do not scaffold the whole app.

---

## Network & Sources

9. **Every external source lives behind a `Source` class with a uniform interface.**
   The Fetcher never contains source-specific parsing. Adding a new source must never require editing the Fetcher.

10. **Every `Source` returns a list of normalised dicts with EXACTLY these keys:**
    `source`, `external_id`, `title`, `company`, `location`, `url`, `description`, `posted_at`, `raw`.
    Missing values are `None` — never empty string, never `"N/A"`.

11. **All network calls go through one shared helper.**
    The helper enforces a 10 s timeout, 2 retry attempts with exponential backoff, and a real `User-Agent` header.
    No bare `requests.get()` anywhere else in the codebase.

12. **A source failing must never kill the cycle.**
    Catch failures per-source, log to `cycle_log` with `status="failed"`, and continue to the next source.
    One dead job board must not stop the others.

13. **Secrets come from environment variables loaded via a `.env` file that is gitignored.**
    Never a literal key in code, never a key in `config.yaml`.
    If a required key is missing, that source skips itself with a clear log line — it does not crash the cycle.

14. **Respect the source.**
    Rate-limit to at most 1 request per second per source, set a real `User-Agent`, and honour any documented page limits.
