"""
Scorer agent — assigns a deterministic fit_score (0-100) to every unscored listing.

Scoring breakdown:
  - Role relevance  (0-40 pts): title alignment with target role signals
  - Location        (0-20 pts): Bengaluru/Bangalore=20, Remote/Hybrid=15, India=10
  - Skills match    (0-30 pts): proportion of config.my_skills found in description
  - Experience      (0-10 pts): experience_years alignment with job requirements

No LLM, no randomness. Same inputs always produce the same score.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config

NAME = "Scorer"

_STRONG_ROLE_SIGNALS = [
    "data analyst",
    "business analyst",
    "bi analyst",
    "analytics analyst",
    "reporting analyst",
    "insights analyst",
]

_WEAK_ROLE_SIGNALS = [
    "business intelligence",
    "data specialist",
    "data engineer",
    "analytics",
]

_EXP_PATTERNS = [
    ("0", "1", "0-1", "entry", "fresher", "junior", "associate"),   # 0-1 years
    ("1", "2", "1-2", "0-2"),                                        # 1-2 years
    ("2", "3", "2-3", "1-3"),                                        # 2-3 years
    ("3", "4", "3-4", "2-4"),                                        # 3-4 years
    ("4", "5", "4-5", "3-5", "senior", "lead"),                     # 4-5+ years
]
_EXP_YEAR_MAP = {token: idx for idx, bucket in enumerate(_EXP_PATTERNS) for token in bucket}


def _score_role(title: str) -> int:
    """0-40: how well the title matches analyst role signals."""
    t = title.lower()
    for sig in _STRONG_ROLE_SIGNALS:
        if sig in t:
            return 40
    for sig in _WEAK_ROLE_SIGNALS:
        if sig in t:
            return 20
    return 0


def _score_location(location: str) -> tuple[int, str]:
    """0-20: location relevance. Returns (score, reason)."""
    loc = (location or "").lower().replace("bangalore", "bengaluru")
    if "bengaluru" in loc:
        return 20, "Bengaluru/Bangalore"
    if "remote" in loc or "hybrid" in loc:
        return 15, "Remote/Hybrid"
    if "india" in loc:
        return 10, "India"
    return 0, f"non-target location ({location})"


def _score_skills(description: str, my_skills: list[str]) -> tuple[int, list[str]]:
    """
    0-30: proportion of my_skills found in description.
    Returns (score, list of matched skills).
    """
    if not my_skills:
        return 0, []
    desc = (description or "").lower()
    matched = [s for s in my_skills if s.lower() in desc]
    proportion = len(matched) / len(my_skills)
    score = round(proportion * 30)
    return score, matched


def _score_experience(description: str, experience_years: int) -> tuple[int, str]:
    """
    0-10: how well the job's experience requirement aligns with config.experience_years.
    Looks for patterns like "2 years", "2-3 years", "senior", "fresher", etc.
    """
    desc = (description or "").lower()

    # Map user experience to a bucket index (0=entry … 4=senior)
    user_bucket = min(experience_years // 1, 4)

    best_match_bucket: int | None = None
    for token, bucket in _EXP_YEAR_MAP.items():
        if token in desc:
            if best_match_bucket is None or abs(bucket - user_bucket) < abs(best_match_bucket - user_bucket):
                best_match_bucket = bucket

    if best_match_bucket is None:
        # No explicit requirement found — neutral
        return 5, "no explicit experience requirement"

    diff = abs(best_match_bucket - user_bucket)
    if diff == 0:
        return 10, f"experience aligns (bucket {best_match_bucket})"
    elif diff == 1:
        return 5, f"experience close (diff={diff})"
    else:
        return 0, f"experience mismatch (diff={diff})"


def _compute_score(listing: dict, config: Config) -> tuple[int, str]:
    """Return (fit_score 0-100, human-readable fit_reason)."""
    title = listing.get("title") or ""
    location = listing.get("location") or ""
    description = listing.get("description") or ""

    role_pts = _score_role(title)
    loc_pts, loc_reason = _score_location(location)
    skills_pts, matched_skills = _score_skills(description, config.my_skills)
    exp_pts, exp_reason = _score_experience(description, config.experience_years)

    total = role_pts + loc_pts + skills_pts + exp_pts

    reason_parts = [
        f"role={role_pts}/40",
        f"location={loc_pts}/20 ({loc_reason})",
        f"skills={skills_pts}/30 ({len(matched_skills)} matched: {', '.join(matched_skills) or 'none'})",
        f"experience={exp_pts}/10 ({exp_reason})",
    ]
    reason = " | ".join(reason_parts)

    return total, reason


class Scorer:
    name: str = NAME

    def run(self, config: Config) -> AgentResult:
        started = datetime.now(timezone.utc).isoformat()

        listings = storage.get_unscored_listings(config.db_path)
        if not listings:
            notes = "No unscored listings found."
            storage.log_cycle(
                path=config.db_path,
                agent=NAME,
                started_at=started,
                records_touched=0,
                status="ok",
                notes=notes,
            )
            return AgentResult(agent=NAME, status="ok", records_touched=0, notes=notes)

        scored_count = 0
        score_distribution: dict[str, int] = {"0-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}

        for listing in listings:
            fit_score, fit_reason = _compute_score(listing, config)
            storage.update_listing_score(
                path=config.db_path,
                listing_id=listing["id"],
                fit_score=fit_score,
                fit_reason=fit_reason,
            )
            scored_count += 1

            if fit_score < 40:
                score_distribution["0-39"] += 1
            elif fit_score < 60:
                score_distribution["40-59"] += 1
            elif fit_score < 80:
                score_distribution["60-79"] += 1
            else:
                score_distribution["80-100"] += 1

        dist_str = " | ".join(f"{k}: {v}" for k, v in score_distribution.items())
        notes = f"Scored {scored_count} listings. Distribution: {dist_str}"

        storage.log_cycle(
            path=config.db_path,
            agent=NAME,
            started_at=started,
            records_touched=scored_count,
            status="ok",
            notes=notes,
        )

        return AgentResult(
            agent=NAME,
            status="ok",
            records_touched=scored_count,
            notes=notes,
        )
