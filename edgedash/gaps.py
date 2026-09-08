"""
Gap report CLI — readable terminal output from gap_snapshots.

Commands
--------
    python -m edgedash.gaps            # latest snapshot table
    python -m edgedash.gaps --trend    # earliest vs latest snapshot, top-10 comparison

Read-only. Never writes to the database. No LLM. No estimation.
"""

from __future__ import annotations

import sys
from typing import Any

from edgedash import storage
from edgedash.config import load_config

_BAR_WIDTH = 20
TOP_N      = 10


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bar(value: float, max_value: float) -> str:
    """Render a text progress bar proportional to max_value."""
    if max_value <= 0:
        return ""
    filled = max(0, min(_BAR_WIDTH, int(round(_BAR_WIDTH * value / max_value))))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _ts(iso: str) -> str:
    """Trim ISO timestamp to 'YYYY-MM-DD HH:MM' for display."""
    return iso[:16].replace("T", " ")


# ---------------------------------------------------------------------------
# Latest snapshot view  (default, no flag)
# ---------------------------------------------------------------------------

def _print_latest(db_path: str) -> None:
    rows = storage.get_latest_gap_snapshot(db_path)

    if not rows:
        print("\n  No gap snapshots found. Run a full cycle first.")
        sys.exit(0)

    computed_at = rows[0].get("computed_at", "")
    run_id      = rows[0].get("run_id", "?")[:8]

    print(f"\n  EdgeDash — Skill Gap Report")
    print(f"  Run: {run_id}  ·  Computed: {_ts(computed_at)} UTC")
    print(f"  Showing {len(rows)} gaps ranked by opportunity cost\n")

    sep = "─" * 98
    print(f"  {sep}")
    print(
        f"  {'#':>3}  {'SKILL':<22}  {'BLOCKED':>7}  "
        f"{'OPP COST':>9}  {'MEAN':>6}  {'TOP':>4}  "
        f"{'CONFIDENCE':<12}  BAR"
    )
    print(f"  {sep}")

    max_cost = rows[0]["opportunity_cost"] if rows else 1.0

    for i, row in enumerate(rows, start=1):
        skill      = row["skill"]
        blocked    = row["listings_blocked"]
        cost       = row["opportunity_cost"]
        mean_s     = row["mean_score"]
        top_s      = row["top_score"]
        low_conf   = row["low_confidence"]
        nice       = row["also_nice_to_have"]
        conf_label = "⚠ low (n<3)" if low_conf else "ok"
        bar        = _bar(cost, max_cost)
        nice_note  = f"  +{nice} nice-to-have" if nice else ""

        print(
            f"  {i:>3}  {skill:<22}  {blocked:>7}  "
            f"{cost:>9.1f}  {mean_s:>6.1f}  {top_s:>4}  "
            f"{conf_label:<12}  {bar}{nice_note}"
        )

    print(f"  {sep}")
    print(
        f"\n  Opportunity cost = sum of (fit_score ÷ 100) per listing blocked by each gap.\n"
        f"  Max cost per listing is 1.0 (a perfect-fit job you can't land due to this gap).\n"
        f"  ⚠ = computed from fewer than 3 listings — treat as indicative only.\n"
        f"  Run `python -m edgedash.skills --audit` to find missing aliases.\n"
        f"  Run `python -m edgedash.gaps --trend` to compare across runs.\n"
    )


# ---------------------------------------------------------------------------
# Trend view  (--trend)
# ---------------------------------------------------------------------------

def _snapshot_to_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Key snapshot rows by skill name for O(1) lookup."""
    return {r["skill"]: r for r in rows}


def _print_trend(db_path: str) -> None:
    runs = storage.get_distinct_gap_runs(db_path)

    print(f"\n  EdgeDash — Skill Gap Trend")

    if not runs:
        print("  No gap snapshots found. Run a full cycle first.\n")
        sys.exit(0)

    if len(runs) == 1:
        ts = _ts(runs[0]["computed_at"])
        print(
            f"\n  Only one snapshot exists (run {runs[0]['run_id'][:8]}, {ts} UTC).\n"
            f"\n  Trend comparison needs at least 2 runs.\n"
            f"  Run the full cycle again — ideally after fetching new listings —\n"
            f"  and then re-run this command to see how gaps are changing.\n"
            f"  There is no trend to report yet. Nothing has been fabricated.\n"
        )
        sys.exit(0)

    # Compare earliest vs latest run
    earliest_run = runs[0]
    latest_run   = runs[-1]

    earliest_rows = storage.get_gap_snapshot_by_run(db_path, earliest_run["run_id"])
    latest_rows   = storage.get_gap_snapshot_by_run(db_path, latest_run["run_id"])

    earliest_map  = _snapshot_to_map(earliest_rows)
    latest_map    = _snapshot_to_map(latest_rows)

    # Top 10 from the latest snapshot is the reference ranking
    top_latest   = latest_rows[:TOP_N]
    top_e_skills = {r["skill"] for r in earliest_rows[:TOP_N]}
    top_l_skills = {r["skill"] for r in top_latest}

    print(
        f"\n  Comparing  earliest: {_ts(earliest_run['computed_at'])} UTC  "
        f"(run {earliest_run['run_id'][:8]})"
    )
    print(
        f"       vs     latest: {_ts(latest_run['computed_at'])} UTC  "
        f"(run {latest_run['run_id'][:8]})"
    )
    print(f"  Window: {len(runs)} snapshots total\n")

    sep = "─" * 100
    print(f"  {sep}")
    print(
        f"  {'#':>3}  {'SKILL':<22}  {'EARLIEST':>9}  {'LATEST':>9}  "
        f"{'CHANGE':>9}  {'CHG%':>7}  STATUS"
    )
    print(f"  {sep}")

    for i, row in enumerate(top_latest, start=1):
        skill      = row["skill"]
        cost_new   = row["opportunity_cost"]

        if skill not in earliest_map:
            # skill didn't exist in the earliest snapshot's data at all
            status    = "NEW ✦"
            cost_old  = 0.0
            change    = cost_new
            change_pct = "  n/a"
        else:
            old       = earliest_map[skill]
            cost_old  = old["opportunity_cost"]
            change    = cost_new - cost_old
            if cost_old > 0:
                change_pct = f"{change / cost_old * 100:+.1f}%"
            else:
                change_pct = "  n/a"

            if skill not in top_e_skills:
                status = "↑ entered top10"
            elif change > 0:
                status = f"▲ +{change:.1f}"
            elif change < 0:
                status = f"▼ {change:.1f}"
            else:
                status = "→ flat"

        old_str = f"{cost_old:>9.1f}" if cost_old else f"{'—':>9}"

        print(
            f"  {i:>3}  {skill:<22}  {old_str}  {cost_new:>9.1f}  "
            f"{change:>+9.1f}  {change_pct:>7}  {status}"
        )

    # Skills that were in the earliest top-10 but dropped out of the latest top-10
    dropped = top_e_skills - top_l_skills
    if dropped:
        print(f"\n  {'─'*60}")
        print(f"  DROPPED OUT of top 10 since earliest snapshot:")
        for skill in sorted(dropped):
            old_cost = earliest_map.get(skill, {}).get("opportunity_cost", 0.0)
            new_cost = latest_map.get(skill, {}).get("opportunity_cost")
            new_str  = f"{new_cost:.1f}" if new_cost is not None else "not in latest"
            print(f"    • {skill:<22}  was {old_cost:.1f}  →  now {new_str}")

    print(f"\n  {sep}")
    print(
        f"\n  Opportunity cost = sum of (fit_score ÷ 100) per listing blocked by each gap.\n"
        f"  Change is (latest − earliest). No interpolation. No projection.\n"
        f"  Run `python -m edgedash.gaps` for the full latest snapshot table.\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    cfg = load_config()
    if "--trend" in sys.argv:
        _print_trend(cfg.db_path)
    else:
        _print_latest(cfg.db_path)


if __name__ == "__main__":
    _main()
