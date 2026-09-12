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
# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------

@tool(
    name="companies_hiring",
    description=(
        "Companies with job listings posted in the last N days, grouped by "
        "company with listing counts. Use this to answer questions about "
        "which companies are actively hiring. An optional keyword can filter "
        "the jobs first, such as Data Analyst."
    ),
    params={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": (
                    "Number of days to look back "
                    "(default: 90, range: 1-90)"
                ),
                "default": 90,
            },
            "keyword": {
                "type": "string",
                "description": (
                    "Optional job keyword or role to filter listings "
                    "before grouping companies, such as Data Analyst."
                ),
                "default": "",
            },
        },
    },
)
def companies_hiring(
    days: int = 90,
    keyword: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """
    Companies with listings posted in the last N days.

    Optionally filters listings by a keyword before grouping
    companies.
    """
    config = load_config()

    days = _clamp_int(days, 1, 90)
    keyword = keyword.strip()

    last_passing = storage.get_last_passing_cycle(config.db_path)
    if not last_passing:
        return [], "No passing cycle found"

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()

    placeholder = "%s" if _is_postgres() else "?"
    like_operator = "ILIKE" if _is_postgres() else "LIKE"

    conditions = [f"posted_at >= {placeholder}"]
    params: list[Any] = [cutoff]

    if keyword:
        pattern = f"%{keyword}%"

        conditions.append(
            f"""
            (
                title {like_operator} {placeholder}
                OR company {like_operator} {placeholder}
                OR location {like_operator} {placeholder}
                OR description {like_operator} {placeholder}
            )
            """
        )

        params.extend(
            [pattern, pattern, pattern, pattern]
        )

    listings = storage.get_listings(
        config.db_path,
        limit=None,
        where_clause=" AND ".join(conditions),
        params=tuple(params),
    )

    company_counts: dict[str, int] = {}

    for listing in listings:
        company = listing.get("company") or "Unknown"
        company_counts[company] = (
            company_counts.get(company, 0) + 1
        )

    rows = [
        {"company": company, "count": count}
        for company, count in sorted(
            company_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )
    ]

    if keyword:
        summary = (
            f"{len(rows)} companies hiring for "
            f"'{keyword}' in the last {days} days"
        )
    else:
        summary = (
            f"{len(rows)} companies from the last {days} days"
        )

    return rows, summary


@tool(
    name="search_listings",
    description=(
        "Search job listings using optional keyword, location, and "
        "required skills filters. Use this to answer questions about "
        "jobs mentioning a skill, technology, company, title, or "
        "location. When multiple skills are provided, all skills "
        "must match."
    ),
    params={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": (
                    "Optional keyword to search in job title, company, "
                    "location, or description."
                ),
                "default": "",
            },
            "location": {
                "type": "string",
                "description": (
                    "Optional job location, such as Bengaluru, "
                    "Mumbai, or Delhi."
                ),
                "default": "",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of skills. When multiple skills "
                    "are provided, the listing must contain all "
                    "of them."
                ),
                "default": [],
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of listings to return "
                    "(default: 10, range: 1-50)."
                ),
                "default": 10,
            },
        },
    },
)
def search_listings(
    keyword: str = "",
    location: str = "",
    skills: list[str] | None = None,
    limit: int = 10,
) -> tuple[list[dict[str, Any]], str]:
    """Search listings using validated parameterised filters."""

    config = load_config()

    keyword = keyword.strip()
    location = location.strip()
    skills = skills or []

    skills = [
        str(skill).strip()
        for skill in skills
        if str(skill).strip()
    ]

    limit = _clamp_int(limit, 1, 50)

    if not keyword and not location and not skills:
        return [], "No search criteria provided"

    placeholder = "%s" if _is_postgres() else "?"
    like_operator = "ILIKE" if _is_postgres() else "LIKE"

    conditions: list[str] = []
    params: list[Any] = []

    # Keyword filter
    if keyword:
        pattern = f"%{keyword}%"

        conditions.append(
            f"""
            (
                title {like_operator} {placeholder}
                OR company {like_operator} {placeholder}
                OR location {like_operator} {placeholder}
                OR description {like_operator} {placeholder}
            )
            """
        )

        params.extend(
            [pattern, pattern, pattern, pattern]
        )

    # Location filter
    if location:
        location_pattern = f"%{location}%"

        conditions.append(
            f"location {like_operator} {placeholder}"
        )

        params.append(location_pattern)

    # All requested skills must match
    for skill in skills:
        skill_pattern = f"%{skill}%"

        conditions.append(
            f"""
            (
                title {like_operator} {placeholder}
                OR description {like_operator} {placeholder}
            )
            """
        )

        params.extend(
            [skill_pattern, skill_pattern]
        )

    where_clause = " AND ".join(conditions)

    rows = storage.get_listings(
        config.db_path,
        limit=limit,
        where_clause=where_clause,
        params=tuple(params),
    )

    criteria: list[str] = []

    if keyword:
        criteria.append(keyword)

    if location:
        criteria.append(location)

    if skills:
        criteria.append(" + ".join(skills))

    summary = (
        f"{len(rows)} listings matching "
        + ", ".join(criteria)
    )

    return rows, summary
