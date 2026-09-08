"""
Tests for edgedash/scoring.py  — pure functions, no I/O, no LLM.

Covers:
  1. Perfect match (all components 1.0)
  2. Zero match (no skills, wrong seniority, on-site, stale)
  3. Empty required_skills (no divide-by-zero)
  4. Null posted_at (recency returns neutral 0.5, no crash)
  5. Null remote_ok (remote returns neutral 0.5, no crash)
  6. Seniority three bands off (component = 0.0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from edgedash.scoring import build_reason, score_listing


# ---------------------------------------------------------------------------
# Minimal Config stub — only the fields scoring.py reads
# ---------------------------------------------------------------------------

@dataclass
class _Cfg:
    my_skills:        list[str]  = field(default_factory=list)
    target_seniority: str        = "mid"
    prefer_remote:    bool       = False
    weight_skills:    float      = 0.50
    weight_seniority: float      = 0.20
    weight_remote:    float      = 0.15
    weight_recency:   float      = 0.15


def _today_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# 1. Perfect match
# ---------------------------------------------------------------------------

def test_perfect_match() -> None:
    cfg = _Cfg(my_skills=["python", "sql"], target_seniority="mid", prefer_remote=True)
    facts = {
        "required_skills": ["python", "sql"],
        "nice_to_have":    [],
        "seniority":       "mid",
        "remote_ok":       True,
        "posted_at":       _today_iso(),
    }
    result = score_listing(facts, cfg)

    assert result["score"] == 100
    assert result["components"]["skills"]    == 1.0
    assert result["components"]["seniority"] == 1.0
    assert result["components"]["remote"]    == 1.0
    assert result["components"]["recency"]   == pytest.approx(1.0, abs=0.05)
    assert result["matched_skills"] == ["python", "sql"]
    assert result["missing_skills"] == []


# ---------------------------------------------------------------------------
# 2. Zero match
# ---------------------------------------------------------------------------

def test_zero_match() -> None:
    cfg = _Cfg(my_skills=["python", "sql"], target_seniority="mid", prefer_remote=True)
    facts = {
        "required_skills": ["java", "c++"],
        "nice_to_have":    [],
        "seniority":       "lead",      # 3 bands from "junior" but 2 from "mid" — still low
        "remote_ok":       False,       # wants remote, listing is on-site
        "posted_at":       _days_ago_iso(30),   # fully stale
    }
    result = score_listing(facts, cfg)

    assert result["score"] < 30           # heavily penalised across all components
    assert result["components"]["skills"] == 0.0
    assert result["components"]["remote"] == 0.0
    assert result["components"]["recency"] == pytest.approx(0.0, abs=0.05)
    assert set(result["missing_skills"]) == {"java", "c++"}


# ---------------------------------------------------------------------------
# 3. Empty required_skills — must not divide by zero
# ---------------------------------------------------------------------------

def test_empty_required_skills_no_divide_by_zero() -> None:
    cfg = _Cfg(my_skills=["python"])
    facts = {
        "required_skills": [],
        "nice_to_have":    ["tableau"],
        "seniority":       "mid",
        "remote_ok":       None,
        "posted_at":       _today_iso(),
    }
    # Must not raise
    result = score_listing(facts, cfg)

    assert isinstance(result["score"], int)
    assert result["components"]["skills"] == 0.0   # 0/0 → 0.0 by convention
    assert result["matched_skills"] == []
    assert result["missing_skills"] == []


# ---------------------------------------------------------------------------
# 4. Null posted_at — recency must return 0.5 neutral, no crash
# ---------------------------------------------------------------------------

def test_null_posted_at_neutral() -> None:
    cfg = _Cfg(my_skills=["sql"])
    facts = {
        "required_skills": ["sql"],
        "nice_to_have":    [],
        "seniority":       "mid",
        "remote_ok":       None,
        "posted_at":       None,
    }
    result = score_listing(facts, cfg)

    assert result["components"]["recency"] == 0.5
    assert isinstance(result["score"], int)


# ---------------------------------------------------------------------------
# 5. Null remote_ok — remote must return 0.5 neutral, no crash
# ---------------------------------------------------------------------------

def test_null_remote_ok_neutral() -> None:
    cfg = _Cfg(my_skills=["sql"], prefer_remote=True)
    facts = {
        "required_skills": ["sql"],
        "nice_to_have":    [],
        "seniority":       "mid",
        "remote_ok":       None,
        "posted_at":       _today_iso(),
    }
    result = score_listing(facts, cfg)

    assert result["components"]["remote"] == 0.5


# ---------------------------------------------------------------------------
# 6. Seniority three bands off → component = 0.0
# ---------------------------------------------------------------------------

def test_seniority_three_bands_off() -> None:
    # target = "junior" (rank 0), listing = "lead" (rank 3) → distance 3 → 0.0
    cfg = _Cfg(my_skills=[], target_seniority="junior")
    facts = {
        "required_skills": [],
        "nice_to_have":    [],
        "seniority":       "lead",
        "remote_ok":       None,
        "posted_at":       None,
    }
    result = score_listing(facts, cfg)

    assert result["components"]["seniority"] == 0.0


# ---------------------------------------------------------------------------
# build_reason smoke tests
# ---------------------------------------------------------------------------

def test_build_reason_includes_gaps() -> None:
    cfg = _Cfg(my_skills=["python"])
    facts_with_match = {
        "required_skills": ["python", "spark"],
        "seniority":       "mid",
        "remote_ok":       True,
        "posted_at":       _days_ago_iso(2),
        "matched_skills":  ["python"],
        "missing_skills":  ["spark"],
    }
    components = {"skills": 0.5, "seniority": 1.0, "remote": 1.0, "recency": 0.93}
    reason = build_reason(components, facts_with_match, cfg)

    assert "1/2 required skills" in reason
    assert "gap: spark" in reason


def test_build_reason_no_gaps() -> None:
    cfg = _Cfg(my_skills=["python", "sql"])
    facts_with_match = {
        "required_skills": ["python"],
        "seniority":       "unknown",
        "remote_ok":       None,
        "posted_at":       None,
        "matched_skills":  ["python"],
        "missing_skills":  [],
    }
    components = {"skills": 1.0, "seniority": 0.5, "remote": 0.5, "recency": 0.5}
    reason = build_reason(components, facts_with_match, cfg)

    assert "gap" not in reason
    assert "1/1 required skills" in reason
