"""Planning — pure function of (state, config), no I/O, deterministic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edgedash.config import Config
from edgedash.state import SystemState


@dataclass
class Task:
    """A single task in the execution plan."""
    agent_name: str
    goal: str
    stop_conditions: dict[str, Any]
    reason: str
    skipped: bool = False


@dataclass
class Plan:
    """Ordered list of tasks to execute (or skip)."""
    tasks: list[Task]

    def render(self) -> str:
        """Return a compact printable plan, one line per agent."""
        lines = []
        for task in self.tasks:
            status = "SKIP" if task.skipped else "RUN "
            stop_str = ", ".join(f"{k}={v}" for k, v in task.stop_conditions.items())
            lines.append(f"  [{status}] {task.agent_name:<18} {task.goal:<30} ({stop_str}) — {task.reason}")
        return "\n".join(lines)


def build_plan(state: SystemState, config: Config) -> Plan:
    """
    Build an execution plan from state and config. Pure function — no I/O.
    Decision rules use thresholds from config.
    Skipped agents appear in the Plan with a reason (rule 31).
    """
    tasks: list[Task] = []

    # Fetch decision
    should_fetch = False
    fetch_reason = ""
    if state.hours_since_fetch is None:
        should_fetch = True
        fetch_reason = "last_fetch_at=null"
    elif state.hours_since_fetch >= config.fetch_interval_hours:
        should_fetch = True
        fetch_reason = f"hours_since_fetch={state.hours_since_fetch:.1f}>={config.fetch_interval_hours}"
    else:
        fetch_reason = f"skipped: hours_since_fetch={state.hours_since_fetch:.1f}<{config.fetch_interval_hours}"

    fetch_stop = {
        "max_pages": config.fetch_max_pages,
        "max_listings": config.fetch_max_listings,
    }
    tasks.append(Task(
        agent_name="fetcher",
        goal="fetch new listings",
        stop_conditions=fetch_stop,
        reason=fetch_reason,
        skipped=not should_fetch,
    ))

    # Score decision
    should_score = state.unscored_count > 0
    score_reason = f"unscored_count={state.unscored_count}" if should_score else f"skipped: unscored_count=0"
    score_stop = {
        "max_items": config.score_batch_size,
        "max_seconds": config.score_max_seconds,
    }
    tasks.append(Task(
        agent_name="scorer",
        goal="score unscored listings",
        stop_conditions=score_stop,
        reason=score_reason,
        skipped=not should_score,
    ))

    # Analyse decision
    should_analyse = state.gaps_stale
    if state.gaps_computed_at is None:
        analyse_reason = "gaps_computed_at=null"
    elif state.gaps_stale:
        analyse_reason = "gaps_stale=true"
    else:
        analyse_reason = "skipped: gaps_stale=false"
    analyse_stop = {
        "max_seconds": config.analyse_max_seconds,
    }
    tasks.append(Task(
        agent_name="gap_analyzer",
        goal="analyze skill gaps",
        stop_conditions=analyse_stop,
        reason=analyse_reason,
        skipped=not should_analyse,
    ))

    return Plan(tasks=tasks)
