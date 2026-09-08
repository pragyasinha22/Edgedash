"""
Verification — deterministic Python checks for output plausibility.
No LLM, no I/O, pure functions only. Per rules 34-39.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import statistics


@dataclass
class CheckResult:
    """Result of a single verification check."""
    name: str
    passed: bool
    observed: Any
    threshold: Any
    message: str


@dataclass
class Verdict:
    """Overall verification verdict from running all checks."""
    passed: bool
    failed_checks: list[CheckResult]
    summary: str


def check_score_spread(scores: list[int], config) -> CheckResult:
    """
    FAILS if max - min < min_score_spread, or if stdev < min_score_stdev.
    Catches the inflation failure mode (all scores clustered together).
    Passes trivially if fewer than 5 scores.
    """
    min_score_spread = getattr(config, "min_score_spread", 10)
    min_score_stdev = getattr(config, "min_score_stdev", 5)

    if len(scores) < 5:
        return CheckResult(
            name="score_spread",
            passed=True,
            observed=f"n={len(scores)}",
            threshold=f"min_n=5",
            message=f"Too few scores to check spread (n={len(scores)} < 5)",
        )

    score_min = min(scores)
    score_max = max(scores)
    spread = score_max - score_min
    stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0

    if spread < min_score_spread:
        return CheckResult(
            name="score_spread",
            passed=False,
            observed=f"spread={spread}",
            threshold=f"min_spread={min_score_spread}",
            message=f"Score spread too narrow: {spread} < {min_score_spread}",
        )

    if stdev < min_score_stdev:
        return CheckResult(
            name="score_spread",
            passed=False,
            observed=f"stdev={stdev:.2f}",
            threshold=f"min_stdev={min_score_stdev}",
            message=f"Score standard deviation too low: {stdev:.2f} < {min_score_stdev}",
        )

    return CheckResult(
        name="score_spread",
        passed=True,
        observed=f"spread={spread}, stdev={stdev:.2f}",
        threshold=f"min_spread={min_score_spread}, min_stdev={min_score_stdev}",
        message=f"Score spread acceptable: spread={spread}, stdev={stdev:.2f}",
    )


def check_extraction_sanity(facts_list: list[dict], config) -> CheckResult:
    """
    FAILS if more than max_empty_extraction_pct of listings have an empty
    required_skills list, or if any listing has more than max_skills_per_listing.
    Catches a broken extractor and a model that returned a whole sentence as skills.
    """
    max_empty_extraction_pct = getattr(config, "max_empty_extraction_pct", 20)
    max_skills_per_listing = getattr(config, "max_skills_per_listing", 20)

    if not facts_list:
        return CheckResult(
            name="extraction_sanity",
            passed=True,
            observed="n=0",
            threshold="n>0",
            message="No extractions to check",
        )

    empty_count = 0
    max_skills_seen = 0
    for facts in facts_list:
        required = facts.get("required_skills") or []
        if not required or len(required) == 0:
            empty_count += 1
        max_skills_seen = max(max_skills_seen, len(required))

    empty_pct = (empty_count / len(facts_list)) * 100

    if empty_pct > max_empty_extraction_pct:
        return CheckResult(
            name="extraction_sanity",
            passed=False,
            observed=f"empty_pct={empty_pct:.1f}%",
            threshold=f"max_empty_pct={max_empty_extraction_pct}%",
            message=f"Too many empty extractions: {empty_pct:.1f}% > {max_empty_extraction_pct}%",
        )

    if max_skills_seen > max_skills_per_listing:
        return CheckResult(
            name="extraction_sanity",
            passed=False,
            observed=f"max_skills={max_skills_seen}",
            threshold=f"max_skills={max_skills_per_listing}",
            message=f"Too many skills in one listing: {max_skills_seen} > {max_skills_per_listing}",
        )

    return CheckResult(
        name="extraction_sanity",
        passed=True,
        observed=f"empty_pct={empty_pct:.1f}%, max_skills={max_skills_seen}",
        threshold=f"max_empty_pct={max_empty_extraction_pct}%, max_skills={max_skills_per_listing}",
        message=f"Extraction sanity acceptable: {empty_pct:.1f}% empty, max {max_skills_seen} skills",
    )


def check_gap_sample_size(gaps: list[dict], config) -> CheckResult:
    """
    FAILS if the top-ranked gap was computed from fewer than min_gap_sample listings.
    Catches ranking a rumour (gap based on too few data points).
    """
    min_gap_sample = getattr(config, "min_gap_sample", 3)

    if not gaps:
        return CheckResult(
            name="gap_sample_size",
            passed=True,
            observed="n=0",
            threshold=f"min_sample={min_gap_sample}",
            message="No gaps to check",
        )

    top_gap = gaps[0]  # gaps are sorted by opportunity_cost descending
    sample_size = top_gap.get("listings_blocked", 0)

    if sample_size < min_gap_sample:
        return CheckResult(
            name="gap_sample_size",
            passed=False,
            observed=f"sample={sample_size}",
            threshold=f"min_sample={min_gap_sample}",
            message=f"Top gap sample too small: {sample_size} < {min_gap_sample}",
        )

    return CheckResult(
        name="gap_sample_size",
        passed=True,
        observed=f"sample={sample_size}",
        threshold=f"min_sample={min_gap_sample}",
        message=f"Gap sample size acceptable: {sample_size} listings",
    )


def check_freshness(latest_fetch_at: str | None, config, now: datetime) -> CheckResult:
    """
    FAILS if the newest listing is older than max_data_age_days.
    `now` is a PARAMETER, never datetime.now() inside the function, so this is testable.
    """
    max_data_age_days = getattr(config, "max_data_age_days", 3)

    if not latest_fetch_at:
        return CheckResult(
            name="freshness",
            passed=False,
            observed="null",
            threshold=f"max_age={max_data_age_days} days",
            message="No fetch timestamp available",
        )

    try:
        fetch_dt = datetime.fromisoformat(latest_fetch_at)
        age_days = (now - fetch_dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return CheckResult(
            name="freshness",
            passed=False,
            observed="invalid_timestamp",
            threshold=f"max_age={max_data_age_days} days",
            message=f"Invalid fetch timestamp: {latest_fetch_at}",
        )

    if age_days > max_data_age_days:
        return CheckResult(
            name="freshness",
            passed=False,
            observed=f"age={age_days:.1f} days",
            threshold=f"max_age={max_data_age_days} days",
            message=f"Data too stale: {age_days:.1f} days > {max_data_age_days}",
        )

    return CheckResult(
        name="freshness",
        passed=True,
        observed=f"age={age_days:.1f} days",
        threshold=f"max_age={max_data_age_days} days",
        message=f"Data freshness acceptable: {age_days:.1f} days old",
    )


def run_all_checks(
    scores: list[int],
    facts_list: list[dict],
    gaps: list[dict],
    latest_fetch_at: str | None,
    config,
    now: datetime,
) -> Verdict:
    """
    Runs every verification check and returns the overall verdict.
    Passes only if all checks pass.
    """
    checks = [
        check_score_spread(scores, config),
        check_extraction_sanity(facts_list, config),
        check_gap_sample_size(gaps, config),
        check_freshness(latest_fetch_at, config, now),
    ]

    failed_checks = [c for c in checks if not c.passed]
    passed = len(failed_checks) == 0

    if passed:
        summary = f"All checks passed ({len(checks)} checks)"
    else:
        failed_names = ", ".join(c.name for c in failed_checks)
        summary = f"Failed checks: {failed_names} ({len(failed_checks)}/{len(checks)} failed)"

    return Verdict(
        passed=passed,
        failed_checks=failed_checks,
        summary=summary,
    )
