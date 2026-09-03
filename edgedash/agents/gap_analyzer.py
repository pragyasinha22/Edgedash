"""
GapAnalyzer agent — finds skills mentioned in top-scoring job listings
that are absent from config.my_skills, and writes them to skill_gaps.

Runs only against listings with fit_score >= config.min_fit_score so we
analyze relevant jobs, not the bottom of the barrel.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config

NAME = "GapAnalyzer"

# Known skills vocabulary — extend as needed.
# Matched case-insensitively against job description text.
SKILLS_VOCAB: list[str] = [
    "sql",
    "python",
    "excel",
    "pandas",
    "tableau",
    "power bi",
    "looker",
    "dbt",
    "spark",
    "aws",
    "azure",
    "gcp",
    "r",
    "scala",
    "airflow",
    "kafka",
    "snowflake",
    "redshift",
    "bigquery",
    "data visualization",
    "statistics",
    "machine learning",
    "numpy",
    "matplotlib",
    "seaborn",
    "plotly",
    "metabase",
    "superset",
]


def _extract_skills(description: str) -> set[str]:
    """Return the set of SKILLS_VOCAB entries found in description (lowercase)."""
    desc = (description or "").lower()
    return {skill for skill in SKILLS_VOCAB if skill in desc}


def _build_gap_rows(
    listings: list[dict],
    my_skills_lower: set[str],
    now: str,
) -> dict[str, int]:
    """
    Count how many listings mention each skill that is NOT in my_skills.
    Returns {skill: frequency}.
    """
    gap_counts: dict[str, int] = {}
    for listing in listings:
        required = _extract_skills(listing.get("description") or "")
        gaps = required - my_skills_lower
        for skill in gaps:
            gap_counts[skill] = gap_counts.get(skill, 0) + 1
    return gap_counts


class GapAnalyzer:
    name: str = NAME

    def run(self, config: Config) -> AgentResult:
        started = datetime.now(timezone.utc).isoformat()
        now_str = started

        listings = storage.get_scored_listings(config.db_path, config.min_fit_score)
        if not listings:
            notes = f"No listings with fit_score >= {config.min_fit_score} found."
            storage.log_cycle(
                path=config.db_path,
                agent=NAME,
                started_at=started,
                records_touched=0,
                status="ok",
                notes=notes,
            )
            return AgentResult(agent=NAME, status="ok", records_touched=0, notes=notes)

        my_skills_lower = {s.lower() for s in config.my_skills}
        gap_counts = _build_gap_rows(listings, my_skills_lower, now_str)

        if gap_counts:
            gap_rows = [
                {"skill": skill, "frequency": freq, "last_seen": now_str}
                for skill, freq in gap_counts.items()
            ]
            storage.upsert_skill_gaps(config.db_path, gap_rows)

        # Top 5 gaps for the notes summary
        top_gaps = sorted(gap_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_str = ", ".join(f"{s}({n})" for s, n in top_gaps) if top_gaps else "none"
        notes = (
            f"Analyzed {len(listings)} listings (score >= {config.min_fit_score}). "
            f"Found {len(gap_counts)} gap skills. Top gaps: {top_str}"
        )

        storage.log_cycle(
            path=config.db_path,
            agent=NAME,
            started_at=started,
            records_touched=len(listings),
            status="ok",
            notes=notes,
        )

        return AgentResult(
            agent=NAME,
            status="ok",
            records_touched=len(listings),
            notes=notes,
        )
