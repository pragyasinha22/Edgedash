"""
Storage module — the ONLY place database drivers are imported.
Swapping to Postgres means changing this file only.
Supports both SQLite (local dev) and Postgres (deployment).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Postgres support - optional, only imported if DATABASE_URL is set
try:
    import psycopg
    _HAS_POSTGRES = True
except ImportError:
    _HAS_POSTGRES = False

logger = logging.getLogger(__name__)

# Backend detection
# _USE_POSTGRES = os.getenv("DATABASE_URL") is not None
# _DATABASE_URL = os.getenv("DATABASE_URL")
_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_USE_POSTGRES = bool(_DATABASE_URL)
_SQLITE_PATH = os.getenv("DB_PATH", "edgedash.db")

# Log backend at startup
if _USE_POSTGRES:
    logger.info("Storage backend: Postgres (DATABASE_URL set)")
else:
    logger.info(f"Storage backend: SQLite (path: {_SQLITE_PATH})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection_string() -> str:
    """Return the connection string for the active backend."""
    if _USE_POSTGRES:
        return _DATABASE_URL
    return f"file:{_SQLITE_PATH}?mode=rwc"


def _connect(path: str | None = None):
    """Return a connection for the active backend."""
    if _USE_POSTGRES:
        if not _HAS_POSTGRES:
            raise RuntimeError("Postgres requested but psycopg not installed. Install: pip install psycopg[binary]")
        conn = psycopg.connect(_DATABASE_URL)
        conn.autocommit = True
        return conn
    else:
        db_path = path or _SQLITE_PATH
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


def _is_postgres() -> bool:
    """Return True if using Postgres backend."""
    return _USE_POSTGRES


def make_listing_id(source: str, url: str) -> str:
    """Stable, dedup-safe hash of source + url."""
    raw = f"{source}::{url}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schema - dialect-aware DDL
# ---------------------------------------------------------------------------

_DDL_SQLITE = [
    """
    CREATE TABLE IF NOT EXISTS listings (
        id          TEXT PRIMARY KEY,
        title       TEXT,
        company     TEXT,
        location    TEXT,
        url         TEXT,
        description TEXT,
        source      TEXT,
        posted_at   TEXT,
        fetched_at  TEXT,
        fit_score   INTEGER,
        fit_reason  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_gaps (
        skill     TEXT PRIMARY KEY,
        frequency INTEGER,
        last_seen TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cycle_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        agent           TEXT,
        started_at      TEXT,
        finished_at     TEXT,
        records_touched INTEGER,
        status          TEXT,
        notes           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extraction_cache (
        description_hash TEXT PRIMARY KEY,
        required_skills  TEXT,
        nice_to_have     TEXT,
        seniority        TEXT,
        remote_ok        INTEGER,
        cached_at        TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gap_snapshots (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id            TEXT NOT NULL,
        computed_at       TEXT NOT NULL,
        skill             TEXT NOT NULL,
        listings_blocked  INTEGER NOT NULL,
        opportunity_cost  REAL NOT NULL,
        mean_score        REAL NOT NULL,
        top_score         INTEGER NOT NULL,
        example_ids       TEXT NOT NULL,
        also_nice_to_have INTEGER NOT NULL DEFAULT 0,
        low_confidence    INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS query_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        question    TEXT NOT NULL,
        tool_used   TEXT,
        params      TEXT,
        answerable  INTEGER NOT NULL,
        reason      TEXT,
        duration_ms INTEGER,
        asked_at    TEXT NOT NULL
    )
    """,
]

_DDL_POSTGRES = [
    """
    CREATE TABLE IF NOT EXISTS listings (
        id          TEXT PRIMARY KEY,
        title       TEXT,
        company     TEXT,
        location    TEXT,
        url         TEXT,
        description TEXT,
        source      TEXT,
        posted_at   TEXT,
        fetched_at  TEXT,
        fit_score   INTEGER,
        fit_reason  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skill_gaps (
        skill     TEXT PRIMARY KEY,
        frequency INTEGER,
        last_seen TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cycle_log (
        id              SERIAL PRIMARY KEY,
        agent           TEXT,
        started_at      TEXT,
        finished_at     TEXT,
        records_touched INTEGER,
        status          TEXT,
        notes           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extraction_cache (
        description_hash TEXT PRIMARY KEY,
        required_skills  TEXT,
        nice_to_have     TEXT,
        seniority        TEXT,
        remote_ok        INTEGER,
        cached_at        TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gap_snapshots (
        id                SERIAL PRIMARY KEY,
        run_id            TEXT NOT NULL,
        computed_at       TEXT NOT NULL,
        skill             TEXT NOT NULL,
        listings_blocked  INTEGER NOT NULL,
        opportunity_cost  REAL NOT NULL,
        mean_score        REAL NOT NULL,
        top_score         INTEGER NOT NULL,
        example_ids       TEXT NOT NULL,
        also_nice_to_have INTEGER NOT NULL DEFAULT 0,
        low_confidence    INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS query_log (
        id          SERIAL PRIMARY KEY,
        question    TEXT NOT NULL,
        tool_used   TEXT,
        params      TEXT,
        answerable  INTEGER NOT NULL,
        reason      TEXT,
        duration_ms INTEGER,
        asked_at    TEXT NOT NULL
    )
    """,
]


def _get_ddl() -> list[str]:
    """Return the appropriate DDL for the active backend."""
    return _DDL_POSTGRES if _is_postgres() else _DDL_SQLITE

# Columns added after initial schema — dialect-aware migrations
_MIGRATIONS_SQLITE = [
    "ALTER TABLE listings ADD COLUMN scored_at TEXT",
    "ALTER TABLE listings ADD COLUMN score_components TEXT",
]

_MIGRATIONS_POSTGRES = [
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS scored_at TEXT",
    "ALTER TABLE listings ADD COLUMN IF NOT EXISTS score_components TEXT",
]


def _get_migrations() -> list[str]:
    """Return the appropriate migrations for the active backend."""
    return _MIGRATIONS_POSTGRES if _is_postgres() else _MIGRATIONS_SQLITE


def init_db(path: str) -> None:
    """Create all tables and apply any pending column migrations."""
    ddl_list = _get_ddl()
    migrations = _get_migrations()
    
    with _connect(path) as conn:
        for ddl in ddl_list:
            conn.execute(ddl)
        
        # Apply migrations
        for migration in migrations:
            try:
                conn.execute(migration)
            except Exception as e:
                # Column already exists - safe to ignore
                if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                    pass
                else:
                    raise


# ---------------------------------------------------------------------------
# Listings — write
# ---------------------------------------------------------------------------

def upsert_listings(path: str, rows: list[dict[str, Any]]) -> int:
    """
    Insert new listings, skip duplicates by primary key.
    Returns the count of genuinely NEW rows inserted.
    """
    if _is_postgres():
        sql = """
            INSERT INTO listings
                (id, title, company, location, url, description,
                 source, posted_at, fetched_at, fit_score, fit_reason)
            VALUES
                (%(id)s, %(title)s, %(company)s, %(location)s, %(url)s, %(description)s,
                 %(source)s, %(posted_at)s, %(fetched_at)s, %(fit_score)s, %(fit_reason)s)
            ON CONFLICT (id) DO NOTHING
        """
    else:
        sql = """
            INSERT OR IGNORE INTO listings
                (id, title, company, location, url, description,
                 source, posted_at, fetched_at, fit_score, fit_reason)
            VALUES
                (:id, :title, :company, :location, :url, :description,
                 :source, :posted_at, :fetched_at, :fit_score, :fit_reason)
        """
    
    now = _now_iso()
    prepped = [
        {
            "id": row.get("id") or make_listing_id(row["source"], row["url"] or ""),
            "title": row.get("title"),
            "company": row.get("company"),
            "location": row.get("location"),
            "url": row.get("url"),
            "description": row.get("description"),
            "source": row.get("source"),
            "posted_at": row.get("posted_at"),
            "fetched_at": row.get("fetched_at") or now,
            "fit_score": row.get("fit_score"),
            "fit_reason": row.get("fit_reason"),
        }
        for row in rows
    ]

    with _connect(path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        conn.executemany(sql, prepped)
        after = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    return after - before


def update_listing_score(
    path: str,
    listing_id: str,
    fit_score: int,
    fit_reason: str,
    score_components: dict | None = None,
) -> None:
    """Write fit_score, fit_reason, score_components, and scored_at to a single listing row."""
    components_json = json.dumps(score_components) if score_components else None
    
    if _is_postgres():
        sql = """UPDATE listings
                   SET fit_score = %s, fit_reason = %s, score_components = %s, scored_at = %s
                   WHERE id = %s"""
        params = (fit_score, fit_reason, components_json, _now_iso(), listing_id)
    else:
        sql = """UPDATE listings
                   SET fit_score = ?, fit_reason = ?, score_components = ?, scored_at = ?
                   WHERE id = ?"""
        params = (fit_score, fit_reason, components_json, _now_iso(), listing_id)
    
    with _connect(path) as conn:
        conn.execute(sql, params)


# ---------------------------------------------------------------------------
# Listings — read
# ---------------------------------------------------------------------------

def count_unscored(path: str) -> int:
    """Return the number of listings without a fit_score."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE fit_score IS NULL"
        ).fetchone()
    return row[0]


def get_unscored_listings(path: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Return listings that have not been scored yet, optionally capped at *limit*."""
    sql = "SELECT * FROM listings WHERE fit_score IS NULL"
    params: tuple = ()
    if limit is not None:
        if _is_postgres():
            sql += " LIMIT %s"
        else:
            sql += " LIMIT ?"
        params = (limit,)
    with _connect(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_scored_listings(path: str, min_score: int) -> list[dict[str, Any]]:
    """Return listings with fit_score >= min_score, ordered by score descending."""
    if _is_postgres():
        sql = "SELECT * FROM listings WHERE fit_score >= %s ORDER BY fit_score DESC"
        params = (min_score,)
    else:
        sql = "SELECT * FROM listings WHERE fit_score >= ? ORDER BY fit_score DESC"
        params = (min_score,)
    
    with _connect(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def last_fetch_time(path: str) -> str | None:
    """Return the most recent fetched_at from real (non-mock) sources, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) FROM listings WHERE source != 'mock'"
        ).fetchone()
    return row[0]


def last_gap_snapshot_time(path: str) -> str | None:
    """Return the most recent computed_at from gap_snapshots, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(computed_at) FROM gap_snapshots"
        ).fetchone()
    return row[0]


def last_scored_time(path: str) -> str | None:
    """Return the most recent scored_at from listings, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(scored_at) FROM listings WHERE scored_at IS NOT NULL"
        ).fetchone()
    return row[0]


def last_cycle_time(path: str) -> str | None:
    """Return the most recent finished_at from cycle_log, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(finished_at) FROM cycle_log"
        ).fetchone()
    return row[0]


def last_cycle_status(path: str) -> str | None:
    """Return the status of the most recent cycle, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT status FROM cycle_log ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None


def get_last_passing_cycle(path: str) -> dict | None:
    """Return the most recent cycle with a passing verification verdict, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            """SELECT * FROM cycle_log 
               WHERE status = 'complete' 
               ORDER BY finished_at DESC 
               LIMIT 1"""
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Skill gaps
# ---------------------------------------------------------------------------

def upsert_skill_gaps(path: str, gaps: list[dict]) -> None:
    """
    Upsert skill gap entries, accumulating frequency on repeated runs.
    Each dict must have: {"skill": str, "frequency": int, "last_seen": str}
    """
    if _is_postgres():
        sql = """
            INSERT INTO skill_gaps (skill, frequency, last_seen)
            VALUES (%(skill)s, %(frequency)s, %(last_seen)s)
            ON CONFLICT (skill) DO UPDATE SET
                frequency = skill_gaps.frequency + EXCLUDED.frequency,
                last_seen = EXCLUDED.last_seen
        """
    else:
        sql = """
            INSERT INTO skill_gaps (skill, frequency, last_seen)
            VALUES (:skill, :frequency, :last_seen)
            ON CONFLICT(skill) DO UPDATE SET
                frequency = frequency + excluded.frequency,
                last_seen = excluded.last_seen
        """
    
    with _connect(path) as conn:
        conn.executemany(sql, gaps)


# ---------------------------------------------------------------------------
# Cycle log
# ---------------------------------------------------------------------------

def log_cycle(
    path: str,
    agent: str,
    started_at: str,
    records_touched: int,
    status: str,
    notes: str = "",
) -> None:
    """Write one row to cycle_log for every agent run."""
    if _is_postgres():
        sql = """
            INSERT INTO cycle_log
                (agent, started_at, finished_at, records_touched, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = (agent, started_at, _now_iso(), records_touched, status, notes)
    else:
        sql = """
            INSERT INTO cycle_log
                (agent, started_at, finished_at, records_touched, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (agent, started_at, _now_iso(), records_touched, status, notes)
    
    with _connect(path) as conn:
        conn.execute(sql, params)

# ---------------------------------------------------------------------------
# Extraction cache (rule 18)
# ---------------------------------------------------------------------------

def get_extraction(path: str, description_hash: str) -> dict[str, Any] | None:
    """Return a cached extraction dict, or None on a cache miss."""
    if _is_postgres():
        sql = "SELECT required_skills, nice_to_have, seniority, remote_ok FROM extraction_cache WHERE description_hash = %s"
        params = (description_hash,)
    else:
        sql = "SELECT required_skills, nice_to_have, seniority, remote_ok FROM extraction_cache WHERE description_hash = ?"
        params = (description_hash,)
    
    with _connect(path) as conn:
        row = conn.execute(sql, params).fetchone()

    if row is None:
        return None

    remote_raw = row["remote_ok"]
    remote_ok: bool | None = None if remote_raw is None else bool(remote_raw)

    return {
        "required_skills": json.loads(row["required_skills"]),
        "nice_to_have": json.loads(row["nice_to_have"]),
        "seniority": row["seniority"],
        "remote_ok": remote_ok,
    }


def save_extraction(path: str, description_hash: str, extraction: dict[str, Any]) -> None:
    """Persist an extraction result keyed on the description hash."""
    remote_raw = extraction["remote_ok"]
    remote_int: int | None = None if remote_raw is None else int(remote_raw)

    if _is_postgres():
        sql = """
            INSERT INTO extraction_cache
                (description_hash, required_skills, nice_to_have, seniority, remote_ok, cached_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (description_hash) DO UPDATE SET
                required_skills = EXCLUDED.required_skills,
                nice_to_have = EXCLUDED.nice_to_have,
                seniority = EXCLUDED.seniority,
                remote_ok = EXCLUDED.remote_ok,
                cached_at = EXCLUDED.cached_at
        """
        params = (
            description_hash,
            json.dumps(extraction["required_skills"]),
            json.dumps(extraction["nice_to_have"]),
            extraction["seniority"],
            remote_int,
            _now_iso(),
        )
    else:
        sql = """
            INSERT OR REPLACE INTO extraction_cache
                (description_hash, required_skills, nice_to_have, seniority, remote_ok, cached_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (
            description_hash,
            json.dumps(extraction["required_skills"]),
            json.dumps(extraction["nice_to_have"]),
            extraction["seniority"],
            remote_int,
            _now_iso(),
        )
    
    with _connect(path) as conn:
        conn.execute(sql, params)


# ---------------------------------------------------------------------------
# Gap snapshots (rule 25 — timestamped, never overwritten)
# ---------------------------------------------------------------------------

def save_gap_snapshot(path: str, run_id: str, rows: list[dict[str, Any]]) -> None:
    """
    Insert a batch of gap rows for *run_id*.  Never updates existing rows.
    Each dict must have:
        skill, listings_blocked, opportunity_cost, mean_score, top_score,
        example_ids (JSON list of str), also_nice_to_have, low_confidence
    """
    if _is_postgres():
        sql = """
            INSERT INTO gap_snapshots
                (run_id, computed_at, skill, listings_blocked, opportunity_cost,
                 mean_score, top_score, example_ids, also_nice_to_have, low_confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
    else:
        sql = """
            INSERT INTO gap_snapshots
                (run_id, computed_at, skill, listings_blocked, opportunity_cost,
                 mean_score, top_score, example_ids, also_nice_to_have, low_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    
    now = _now_iso()
    with _connect(path) as conn:
        conn.executemany(
            sql,
            [
                (
                    run_id,
                    now,
                    r["skill"],
                    r["listings_blocked"],
                    r["opportunity_cost"],
                    r["mean_score"],
                    r["top_score"],
                    json.dumps(r["example_ids"]),
                    int(r["also_nice_to_have"]),
                    int(r["low_confidence"]),
                )
                for r in rows
            ],
        )


def get_latest_gap_snapshot(path: str) -> list[dict[str, Any]]:
    """"Return all rows from the most recent gap_snapshots run, ordered by opportunity_cost desc."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT run_id FROM gap_snapshots ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return []
        latest_run = row["run_id"]
        
        if _is_postgres():
            rows = conn.execute(
                """SELECT * FROM gap_snapshots
                   WHERE run_id = %s
                   ORDER BY opportunity_cost DESC""",
                (latest_run,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM gap_snapshots
                   WHERE run_id = ?
                   ORDER BY opportunity_cost DESC""",
                (latest_run,),
            ).fetchall()
    
    result = []
    for r in rows:
        d = dict(r)
        d["example_ids"] = json.loads(d["example_ids"])
        d["low_confidence"] = bool(d["low_confidence"])
        result.append(d)
    return result


def get_gap_snapshot(path: str, run_id: str) -> list[dict[str, Any]]:
    """Return all rows for a specific run_id, ordered by opportunity_cost desc."""
    if _is_postgres():
        sql = """SELECT * FROM gap_snapshots
                   WHERE run_id = %s
                   ORDER BY opportunity_cost DESC"""
        params = (run_id,)
    else:
        sql = """SELECT * FROM gap_snapshots
                   WHERE run_id = ?
                   ORDER BY opportunity_cost DESC"""
        params = (run_id,)
    
    with _connect(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    
    result = []
    for r in rows:
        d = dict(r)
        d["example_ids"] = json.loads(d["example_ids"])
        d["low_confidence"] = bool(d["low_confidence"])
        result.append(d)
    return result


def get_distinct_gap_runs(path: str) -> list[dict[str, Any]]:
    """
    Return one row per distinct run_id, ordered oldest → newest.
    Each row has run_id and computed_at (the timestamp of the first row in that run).
    """
    with _connect(path) as conn:
        rows = conn.execute(
            """SELECT run_id, MIN(computed_at) AS computed_at
               FROM gap_snapshots
               GROUP BY run_id
               ORDER BY computed_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_scored_listings_with_extractions(path: str) -> list[dict[str, Any]]:
    """
    Return every scored listing joined with its extraction cache row.
    Only listings that have BOTH a fit_score AND an extraction are returned —
    unscored listings are intentionally excluded (rule 18).
    """
    # Two-query approach works with both SQLite and Postgres
    with _connect(path) as conn:
        listings = conn.execute(
            "SELECT id, fit_score, posted_at, description FROM listings WHERE fit_score IS NOT NULL"
        ).fetchall()
        cache = conn.execute(
            "SELECT description_hash, required_skills, nice_to_have FROM extraction_cache"
        ).fetchall()

    import hashlib as _hl
    hash_map = {r["description_hash"]: r for r in cache}

    result = []
    for lst in listings:
        desc = lst["description"] or ""
        h = _hl.sha256(desc.encode()).hexdigest()
        ec = hash_map.get(h)
        if ec is None:
            continue   # no extraction yet — skip
        try:
            required = json.loads(ec["required_skills"]) if ec["required_skills"] else []
            nice     = json.loads(ec["nice_to_have"])    if ec["nice_to_have"]    else []
        except (ValueError, TypeError):
            required, nice = [], []
        result.append({
            "id":               lst["id"],
            "fit_score":        lst["fit_score"],
            "posted_at":        lst["posted_at"],
            "required_skills":  required,
            "nice_to_have":     nice,
        })
    return result


# ---------------------------------------------------------------------------
# Query log — for natural language interface
# ---------------------------------------------------------------------------

def log_query(
    path: str,
    question: str,
    tool_used: str | None,
    params: dict | None,
    answerable: bool,
    reason: str | None,
    duration_ms: int,
) -> None:
    """Log a question to the query_log table."""
    if _is_postgres():
        sql = """INSERT INTO query_log
                   (question, tool_used, params, answerable, reason, duration_ms, asked_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        params = (
            question,
            tool_used,
            json.dumps(params) if params else None,
            1 if answerable else 0,
            reason,
            duration_ms,
            _now_iso(),
        )
    else:
        sql = """INSERT INTO query_log
                   (question, tool_used, params, answerable, reason, duration_ms, asked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)"""
        params = (
            question,
            tool_used,
            json.dumps(params) if params else None,
            1 if answerable else 0,
            reason,
            duration_ms,
            _now_iso(),
        )
    
    with _connect(path) as conn:
        conn.execute(sql, params)


def count_queries_today(path: str, date_str: str) -> int:
    """Count queries asked on a specific date (ISO format YYYY-MM-DD)."""
    if _is_postgres():
        sql = """SELECT COUNT(*) FROM query_log
                   WHERE DATE(asked_at::timestamp) = %s"""
        params = (date_str,)
    else:
        sql = """SELECT COUNT(*) FROM query_log
                   WHERE DATE(asked_at) = ?"""
        params = (date_str,)
    
    with _connect(path) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# CLI commands for deployment
# ---------------------------------------------------------------------------

def _migrate() -> None:
    """Create all tables on an empty database. Safe to run repeatedly."""
    print(f"Migrating database ({'Postgres' if _is_postgres() else 'SQLite'})...")
    try:
        db_path = _DATABASE_URL if _is_postgres() else _SQLITE_PATH
        init_db(db_path)
        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}")
        raise


def _check() -> None:
    """Print backend status, connection health, and row counts per table."""
    backend = "Postgres" if _is_postgres() else "SQLite"
    connection_str = _DATABASE_URL if _is_postgres() else _SQLITE_PATH
    
    print(f"Backend: {backend}")
    print(f"Connection: {connection_str}")
    
    try:
        db_path = _DATABASE_URL if _is_postgres() else _SQLITE_PATH
        with _connect(db_path) as conn:
            print("Status: Connected")
            
            tables = ["listings", "skill_gaps", "cycle_log", "extraction_cache", "gap_snapshots", "query_log"]
            for table in tables:
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    count = row[0] if row else 0
                    print(f"  {table}: {count} rows")
                except Exception as e:
                    print(f"  {table}: ERROR - {e}")
    except Exception as e:
        print(f"Status: Failed to connect - {e}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m edgedash.storage --migrate | --check")
        sys.exit(1)
    
    cmd = sys.argv[1]
    if cmd == "--migrate":
        _migrate()
    elif cmd == "--check":
        _check()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m edgedash.storage --migrate | --check")
        sys.exit(1)
