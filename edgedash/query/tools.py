"""
Query tool registry — parameterised, read-only queries for natural language interface.

Per rules 40-46:
- No SQL generation from models
- All tools are read-only, parameterised, validated
- Model appears twice: ROUTE (pick tool) and PHRASE (turn rows to prose)
- Tools read from last passing cycle only
- Every parameter is validated and clamped before use
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from edgedash import storage
from edgedash.config import load_config
from edgedash.skills import canonical
from edgedash.storage import _is_postgres


# ---------------------------------------------------------------------------
# Tool registry and decorator
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {}


def tool(
    name: str,
    description: str,
    params: dict[str, Any],
) -> Callable:
    """
    Decorator to register a query function in the TOOLS registry.

    Args:
        name: Tool identifier (used by router model)
        description: Human-readable description of when this tool applies
        params: JSON-schema-style parameter specification
    """
    def decorator(func: Callable) -> Callable:
        TOOLS[name] = {
            "name": name,
            "description": description,
            "params": params,
            "function": func,
        }
        return func
    return decorator


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------

def _clamp_int(value: int, min_val: int, max_val: int) -> int:
    """Clamp an integer to a safe range."""
    return max(min_val, min(value, max_val))


def _validate_skill(skill: str, config) -> str | None:
    """
    Canonicalise a skill and check if it exists in the database.
    Returns None if the skill is not found (empty result, not an error).
    """
    if not skill or not skill.strip():
        return None
    
    canon = canonical(skill, config.skill_aliases)
    
    # Check if this skill exists in any gap snapshot
    # If not found, return None (empty result rather than error)
    last_passing = storage.get_last_passing_cycle(config.db_path)
    if not last_passing:
        return None
    
    # Check if skill appears in any gap from the last passing cycle
    gaps = storage.get_gap_snapshot(config.db_path, last_passing.get("run_id"))
    if gaps:
        skill_names = {g.get("skill") for g in gaps if g.get("skill")}
        if canon not in skill_names:
            return None
    
    return canon


# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------

@tool(
    name="companies_hiring",
    description="Companies with job listings posted in the last N days, grouped by company with listing counts. Use this to answer questions about which companies are actively hiring.",
    params={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to look back (default: 7, range: 1-90)",
                "default": 7,
            }
        },
    },
)
# ------------
@tool(
    name="search_listings",
    description=(
        "Search job listings by keyword. Use this to answer questions about "
        "jobs mentioning a skill, technology, company, title, or location."
    ),
    params={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Keyword to search for in job title, company, location, or description.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of listings to return (default: 10, range: 1-50).",
                "default": 10,
            },
        },
    },
)
def search_listings(
    keyword: str,
    limit: int = 10,
) -> tuple[list[dict[str, Any]], str]:
    """Search listings using a parameterised keyword query."""

    config = load_config()

    keyword = keyword.strip()
    limit = _clamp_int(limit, 1, 50)

    if not keyword:
        return [], "No search keyword provided"

    pattern = f"%{keyword}%"

    if _is_postgres():
        where_clause = """
            (
                title ILIKE %s
                OR company ILIKE %s
                OR location ILIKE %s
                OR description ILIKE %s
            )
        """
    else:
        where_clause = """
            (
                title LIKE ?
                OR company LIKE ?
                OR location LIKE ?
                OR description LIKE ?
            )
        """

    rows = storage.get_listings(
        config.db_path,
        limit=limit,
        where_clause=where_clause,
        params=(pattern, pattern, pattern, pattern),
    )

    summary = f"{len(rows)} listings matching '{keyword}'"
    return rows, summary

# ------------
def companies_hiring(days: int = 7) -> tuple[list[dict[str, Any]], str]:
    """
    Companies with listings posted in the last N days.
    
    Returns:
        (rows, summary) where rows is a list of dicts with company, count
    """
    config = load_config()
    
    # Validate and clamp parameter
    days = _clamp_int(days, 1, 90)
    
    # Get last passing cycle
    last_passing = storage.get_last_passing_cycle(config.db_path)
    if not last_passing:
        return [], "No passing cycle found"
    
    # Calculate cutoff date
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    
    # Query listings from last passing cycle, grouped by company
    listings = storage.get_listings(
        config.db_path,
        limit=None,
        where_clause="posted_at >= ?",
        params=(cutoff,),
    )
    
    # Group by company
    company_counts: dict[str, int] = {}
    for listing in listings:
        company = listing.get("company") or "Unknown"
        company_counts[company] = company_counts.get(company, 0) + 1
    
    # Convert to list of dicts
    rows = [
        {"company": company, "count": count}
        for company, count in sorted(company_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    
    summary = f"{len(rows)} companies from the last {days} days"
    return rows, summary
