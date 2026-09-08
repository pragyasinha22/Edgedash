"""
Pure scoring functions for EdgeDash — no I/O, no LLM, no side effects.

score_listing(facts, config) -> dict
    Takes extracted facts + config, returns {"score": int, "components": dict}.
    Each component is a float 0.0–1.0. Final score is the weighted sum × 100,
    clamped to [0, 100]. Weights live in config.yaml so you can tune them.

build_reason(components, facts, config) -> str
    Assembles a compact human-readable string from the numbers (rule 19).
    The model never writes this — every word comes from our arithmetic.

Rule 16: no score field is ever sent to the LLM.
Rule 19: reason is generated from components, not from free model text.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config

# ---------------------------------------------------------------------------
# Seniority ordering for distance calculation
# ---------------------------------------------------------------------------

_SENIORITY_ORDER: dict[str, int] = {
    "junior": 0,
    "mid":    1,
    "senior": 2,
    "lead":   3,
    "unknown": -1,   # treated specially — no penalty, no bonus
}


# ---------------------------------------------------------------------------
# Individual component scorers (each returns float 0.0–1.0)
# ---------------------------------------------------------------------------

def _skills_component(facts: dict, config: Config) -> tuple[float, list[str], list[str]]:
    """
    Fraction of required_skills that appear in my_skills (case-insensitive).
    Returns (score 0.0-1.0, matched skills, missing skills).
    Never divides by zero — returns 0.0 if required_skills is empty.
    """
    required: list[str] = facts.get("required_skills") or []
    my: set[str] = {s.lower() for s in (config.my_skills or [])}

    if not required:
        return 0.0, [], []

    matched = [s for s in required if s.lower() in my]
    missing = [s for s in required if s.lower() not in my]
    return len(matched) / len(required), matched, missing


def _seniority_component(facts: dict, config: Config) -> float:
    """
    1.0 if listing seniority matches target_seniority exactly.
    0.6 one band away, 0.25 two bands away, 0.0 three or more bands away.
    0.5 if either side is "unknown" — neutral, no penalty.
    """
    listing_seniority: str = (facts.get("seniority") or "unknown").lower()
    target: str = (getattr(config, "target_seniority", "mid") or "mid").lower()

    if listing_seniority == "unknown" or target == "unknown":
        return 0.5   # neutral — no information, no penalty

    listing_rank = _SENIORITY_ORDER.get(listing_seniority, -1)
    target_rank  = _SENIORITY_ORDER.get(target, -1)

    if listing_rank == -1 or target_rank == -1:
        return 0.5

    distance = abs(listing_rank - target_rank)
    if distance == 0:
        return 1.0
    elif distance == 1:
        return 0.6
    elif distance == 2:
        return 0.25
    else:
        return 0.0


def _remote_component(facts: dict, config: Config) -> float:
    """
    1.0 if listing remote_ok matches config.prefer_remote.
    0.5 if remote_ok is None (not stated — no penalty).
    0.0 if listing explicitly conflicts with preference.
    """
    remote_ok: bool | None = facts.get("remote_ok")
    prefer_remote: bool = getattr(config, "prefer_remote", False)

    if remote_ok is None:
        return 0.5   # not stated — neutral

    if prefer_remote and remote_ok:
        return 1.0
    if not prefer_remote and not remote_ok:
        return 1.0
    if prefer_remote and not remote_ok:
        return 0.0   # wants remote, listing is on-site only
    # doesn't prefer remote, but listing offers it — fine, minor bonus
    return 0.8


def _recency_component(facts: dict) -> float:
    """
    1.0 if posted today, decaying linearly to 0.0 at 30 days.
    Returns 0.5 if posted_at is null or unparseable — no crash, no penalty.
    """
    posted_at: str | None = facts.get("posted_at")
    if not posted_at:
        return 0.5

    try:
        # Accept ISO strings with or without timezone
        if posted_at.endswith("Z"):
            posted_at = posted_at[:-1] + "+00:00"
        posted_dt = datetime.fromisoformat(posted_at)
        if posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_old = (now - posted_dt).total_seconds() / 86_400
        if days_old < 0:
            return 1.0   # future timestamp — treat as fresh
        return max(0.0, 1.0 - days_old / 30.0)
    except (ValueError, TypeError):
        return 0.5   # unparseable date — neutral, never crash


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_listing(facts: dict, config: Config) -> dict[str, Any]:
    """
    Compute a fit score from extracted facts and user config.

    Returns:
        {
          "score": int,            # 0–100, weighted sum of components
          "components": {
              "skills":   float,   # 0.0–1.0
              "seniority": float,
              "remote":   float,
              "recency":  float,
          },
          "matched_skills": list[str],
          "missing_skills": list[str],
        }

    Rule 16: no score field is ever sent to the LLM.
    Rule 18: caller selects only NULL-score listings before calling this.
    """
    w_skills   = float(getattr(config, "weight_skills",   0.50))
    w_seniority = float(getattr(config, "weight_seniority", 0.20))
    w_remote   = float(getattr(config, "weight_remote",   0.15))
    w_recency  = float(getattr(config, "weight_recency",  0.15))

    skills_val, matched, missing = _skills_component(facts, config)
    seniority_val = _seniority_component(facts, config)
    remote_val    = _remote_component(facts, config)
    recency_val   = _recency_component(facts)

    weighted = (
        skills_val   * w_skills
        + seniority_val * w_seniority
        + remote_val    * w_remote
        + recency_val   * w_recency
    )

    # Normalise by total weight so the scale is always 0–1 even if weights don't sum to 1
    total_weight = w_skills + w_seniority + w_remote + w_recency
    normalised = weighted / total_weight if total_weight > 0 else 0.0

    score = int(round(min(100, max(0, normalised * 100))))

    return {
        "score": score,
        "components": {
            "skills":    round(skills_val, 4),
            "seniority": round(seniority_val, 4),
            "remote":    round(remote_val, 4),
            "recency":   round(recency_val, 4),
        },
        "matched_skills": matched,
        "missing_skills": missing,
    }


def build_reason(components: dict, facts: dict, config: Config) -> str:
    """
    Build a compact human-readable reason string from score components (rule 19).
    Every word comes from our arithmetic — the model never touches this.

    Example output:
      "4/6 required skills · seniority fits · remote · posted 2d ago | gap: kubernetes, spark"
    """
    parts: list[str] = []

    # --- skills ---
    required: list[str] = facts.get("required_skills") or []
    matched: list[str]  = facts.get("matched_skills") or []   # passed through from score_listing
    n_req     = len(required)
    n_matched = len(matched)

    if n_req == 0:
        parts.append("no required skills listed")
    else:
        parts.append(f"{n_matched}/{n_req} required skills")

    # --- seniority ---
    s_val = components.get("seniority", 0.5)
    listing_seniority = (facts.get("seniority") or "unknown").lower()
    if listing_seniority == "unknown":
        parts.append("seniority not stated")
    elif s_val == 1.0:
        parts.append("seniority fits")
    elif s_val >= 0.6:
        parts.append(f"seniority close ({listing_seniority})")
    else:
        parts.append(f"seniority mismatch ({listing_seniority})")

    # --- remote ---
    remote_ok: bool | None = facts.get("remote_ok")
    r_val = components.get("remote", 0.5)
    if remote_ok is None:
        parts.append("remote not stated")
    elif remote_ok:
        parts.append("remote")
    else:
        parts.append("on-site only")

    # --- recency ---
    recency_val = components.get("recency", 0.5)
    posted_at: str | None = facts.get("posted_at")
    if posted_at and recency_val != 0.5:
        days_old = round((1.0 - recency_val) * 30)
        if days_old <= 0:
            parts.append("posted today")
        elif days_old == 1:
            parts.append("posted 1d ago")
        else:
            parts.append(f"posted {days_old}d ago")
    else:
        parts.append("post date unknown")

    summary = " · ".join(parts)

    # --- gaps (the most useful part for the GapAnalyzer) ---
    missing: list[str] = facts.get("missing_skills") or []
    if missing:
        gap_str = ", ".join(missing)
        return f"{summary} | gap: {gap_str}"

    return summary
