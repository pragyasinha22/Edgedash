"""
GapAnalyzer agent — deterministic SQL + Python, no LLM (rule 22).

For every scored listing that has an extraction, compares the listing's
required_skills against config.my_skills (after canonicalisation via
skills.canonical). Skills the user lacks are gaps.

Per gap skill, computes:
    listings_blocked  — count of scored listings requiring this skill
    opportunity_cost  — sum of (fit_score / 100) for those listings  ← ranking key (rule 24)
    mean_score        — mean fit_score of those listings
    top_score         — highest fit_score among those listings
    example_ids       — up to 5 listing IDs, highest score first (rule 26)
    also_nice_to_have — count where the skill appeared as nice_to_have only

Ranks gaps by opportunity_cost descending, reports top 10.
Writes a timestamped snapshot to gap_snapshots — never overwrites (rule 25).
Flags gaps from < 3 listings as "low confidence" (rule 27).
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.agents.registry import register_agent
from edgedash.config import Config
from edgedash.skills import canonical

logger = logging.getLogger(__name__)

NAME = "gap_analyzer"
TOP_N = 10
LOW_CONFIDENCE_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Core arithmetic — pure, testable, no I/O
# ---------------------------------------------------------------------------

def compute_gaps(
    listings: list[dict[str, Any]],
    my_skills: set[str],
    aliases: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Compute gap records from a list of scored+extracted listings.

    opportunity_cost(skill) = sum( listing.score / 100 ) for every listing
                              that requires this skill and the user lacks.

    Dividing by 100 keeps the value in [0, N] where N = listings_blocked,
    so the maximum cost per listing is 1.0 (a perfect-fit listing you can't
    land because of this one gap).  Rule 24: a listing scored 85 contributes
    0.85; one scored 20 contributes 0.20.  Raw frequency never drives rank.

    Returns a list of gap dicts sorted by opportunity_cost descending.
    Each dict has:
        skill, listings_blocked, opportunity_cost, mean_score, top_score,
        example_ids, also_nice_to_have, low_confidence
    """
    # Accumulator keyed on canonical skill name
    # value: {"scores": list[int], "ids": list[tuple[int,str]], "nice": int}
    gaps: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"scores": [], "ids": [], "nice": 0}
    )

    for listing in listings:
        score     = listing["fit_score"]
        lid       = listing["id"]
        required  = listing.get("required_skills") or []
        nice      = listing.get("nice_to_have") or []

        nice_canonical = {canonical(s, aliases) for s in nice if s}

        for raw_skill in required:
            canon = canonical(raw_skill, aliases)
            if not canon:
                continue
            if canon in my_skills:
                continue   # user has this skill — not a gap

            gaps[canon]["scores"].append(score)
            gaps[canon]["ids"].append((score, lid))
            # track if it also appears as nice-to-have in any listing
            if canon in nice_canonical:
                gaps[canon]["nice"] += 1

    if not gaps:
        return []

    records: list[dict[str, Any]] = []
    for skill, data in gaps.items():
        scores = data["scores"]
        # Sort IDs by score descending for example_ids (rule 26)
        top_ids = [lid for _, lid in sorted(data["ids"], key=lambda x: x[0], reverse=True)]

        opportunity_cost = round(sum(s / 100.0 for s in scores), 4)  # rule 24: score/100 per listing
        mean_score       = float(statistics.mean(scores))
        top_score        = int(max(scores))
        listings_blocked = len(scores)
        low_confidence   = listings_blocked < LOW_CONFIDENCE_THRESHOLD

        records.append({
            "skill":            skill,
            "listings_blocked": listings_blocked,
            "opportunity_cost": opportunity_cost,
            "mean_score":       round(mean_score, 2),
            "top_score":        top_score,
            "example_ids":      top_ids[:5],          # rule 26 — up to 5
            "also_nice_to_have": data["nice"],
            "low_confidence":   low_confidence,
        })

    records.sort(key=lambda r: r["opportunity_cost"], reverse=True)
    return records


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@register_agent
class GapAnalyzer:
    name: str = NAME

    def __init__(self, config: Config | None = None) -> None:
        # Config needed for registry compatibility, but not used in GapAnalyzer
        pass

    def run(self, config: Config, stop_conditions: dict | None = None) -> AgentResult:
        started = datetime.now(timezone.utc).isoformat()
        started_dt = datetime.now(timezone.utc)

        stop = stop_conditions or {}
        max_seconds = stop.get("max_seconds")

        listings = storage.get_scored_listings_with_extractions(config.db_path)

        if not listings:
            notes = "No scored+extracted listings available yet."
            storage.log_cycle(
                path=config.db_path,
                agent=NAME,
                started_at=started,
                records_touched=0,
                status="ok",
                notes=notes,
            )
            return AgentResult(agent=NAME, status="ok", records_touched=0, notes=notes)

        # Check max_seconds before processing
        if max_seconds is not None:
            elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()
            if elapsed >= max_seconds:
                logger.info("GapAnalyzer: skipped due to max_seconds=%d", max_seconds)
                notes = f"Skipped: max_seconds={max_seconds} exceeded before processing"
                storage.log_cycle(
                    path=config.db_path,
                    agent=NAME,
                    started_at=started,
                    records_touched=0,
                    status="ok",
                    notes=notes,
                )
                return AgentResult(agent=NAME, status="ok", records_touched=0, notes=notes)

        my_skills: set[str] = {
            canonical(s, config.skill_aliases)
            for s in (config.my_skills or [])
            if s
        }

        all_gaps = compute_gaps(listings, my_skills, config.skill_aliases)
        top_gaps = all_gaps[:TOP_N]

        # Write timestamped snapshot — never overwrite (rule 25)
        run_id = uuid.uuid4().hex
        if all_gaps:
            storage.save_gap_snapshot(config.db_path, run_id, all_gaps)

        # Build notes (rule 26 — sample size alongside every aggregate)
        n_listings = len(listings)
        n_gaps     = len(all_gaps)

        if top_gaps:
            top = top_gaps[0]
            conf_flag = " (low-conf)" if top["low_confidence"] else ""
            top_summary = (
                f"top: {top['skill']} "
                f"({top['listings_blocked']} listings, "
                f"cost {top['opportunity_cost']:.1f}){conf_flag}"
            )
        else:
            top_summary = "no gaps found"

        notes = f"{n_gaps} gaps · {top_summary} · {n_listings} listings analysed"

        storage.log_cycle(
            path=config.db_path,
            agent=NAME,
            started_at=started,
            records_touched=n_listings,
            status="ok",
            notes=notes,
        )

        return AgentResult(
            agent=NAME,
            status="ok",
            records_touched=n_listings,
            notes=notes,
        )
