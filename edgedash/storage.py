"""
Storage module — the ONLY place sqlite3 is imported.
Swapping to Postgres in week 4 means changing this file only.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def make_listing_id(source: str, url: str) -> str:
    """Stable, dedup-safe hash of source + url."""
    raw = f"{source}::{url}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = [
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
]


def init_db(path: str) -> None:
    """Create all tables if they don't exist yet."""
    with _connect(path) as conn:
        for ddl in _DDL:
            conn.execute(ddl)


# ---------------------------------------------------------------------------
# Listings — write
# ---------------------------------------------------------------------------

def upsert_listings(path: str, rows: list[dict[str, Any]]) -> int:
    """
    Insert new listings, skip duplicates by primary key.
    Returns the count of genuinely NEW rows inserted.
    """
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
            "fetched_at": row.get("fetched_at") or now,  # always stamp new rows
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
) -> None:
    """Write fit_score and fit_reason back to a single listing row."""
    with _connect(path) as conn:
        conn.execute(
            "UPDATE listings SET fit_score = ?, fit_reason = ? WHERE id = ?",
            (fit_score, fit_reason, listing_id),
        )


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


def get_unscored_listings(path: str) -> list[dict[str, Any]]:
    """Return all listings that have not been scored yet."""
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE fit_score IS NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def get_scored_listings(path: str, min_score: int) -> list[dict[str, Any]]:
    """Return listings with fit_score >= min_score, ordered by score descending."""
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE fit_score >= ? ORDER BY fit_score DESC",
            (min_score,),
        ).fetchall()
    return [dict(r) for r in rows]


def last_fetch_time(path: str) -> str | None:
    """Return the most recent fetched_at from real (non-mock) sources, or None."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) FROM listings WHERE source != 'mock'"
        ).fetchone()
    return row[0]


def get_listings(
    path: str,
    limit: int = 50,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch listings ordered by fit_score descending, with optional score filter."""
    if min_score is not None:
        sql = """
            SELECT * FROM listings
            WHERE fit_score >= ?
            ORDER BY fit_score DESC
            LIMIT ?
        """
        params: tuple = (min_score, limit)
    else:
        sql = "SELECT * FROM listings ORDER BY fit_score DESC LIMIT ?"
        params = (limit,)

    with _connect(path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Skill gaps
# ---------------------------------------------------------------------------

def upsert_skill_gaps(path: str, gaps: list[dict]) -> None:
    """
    Upsert skill gap entries, accumulating frequency on repeated runs.
    Each dict must have: {"skill": str, "frequency": int, "last_seen": str}
    """
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
    sql = """
        INSERT INTO cycle_log
            (agent, started_at, finished_at, records_touched, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    with _connect(path) as conn:
        conn.execute(sql, (agent, started_at, _now_iso(), records_touched, status, notes))
