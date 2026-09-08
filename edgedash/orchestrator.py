"""
Orchestrator — reads state, decides what to run, delegates to agents, logs everything.
State-driven per rules 28-33.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher  # noqa: F401 - ensure registration
from edgedash.agents.gap_analyzer import GapAnalyzer  # noqa: F401 - ensure registration
from edgedash.agents.mock_fetcher import MockFetcher  # noqa: F401 - ensure registration
from edgedash.agents.registry import get_agent, get_fetcher
from edgedash.agents.scorer import Scorer  # noqa: F401 - ensure registration
from edgedash.config import Config
from edgedash.planning import build_plan
from edgedash.state import read_state



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEP = "─" * 60


def _banner(text: str) -> None:
    print(f"\n{_SEP}\n  {text}\n{_SEP}")


def _row(label: str, value: object) -> None:
    print(f"  {label:<22} {value}")


def _result_line(result: AgentResult) -> None:
    icon = "✓" if result.status == "ok" else ("↷" if result.status == "skipped" else "✗")
    print(
        f"  {icon}  {result.agent:<18} "
        f"status={result.status:<8} "
        f"new={result.records_touched:>4}   {result.notes}"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cycle(
    config: Config,
    dry_run: bool = False,
    force_agents: list[str] | None = None,
    explain: bool = False,
) -> None:
    cycle_start = datetime.now(timezone.utc)

    # 1. Init DB
    storage.init_db(config.db_path)

    # 2. Read state (rule 28)
    state = read_state(config, cycle_start)

    _banner("EdgeDash — Cycle Starting")
    _row("Time (UTC):",    cycle_start.isoformat(timespec="seconds"))
    _row("DB path:",       config.db_path)
    _row("Target role:",   config.target_role)
    _row("Target city:",   config.target_city)
    _row("Last fetch:",    state.last_fetch_at or "never")
    _row("Unscored rows:", state.unscored_count)
    _row("Mode:",          "MOCK (offline)" if config.use_mock_fetcher else "LIVE")

    # 3. Print SystemState if --explain
    if explain:
        _banner("SystemState")
        _row("last_fetch_at:", state.last_fetch_at or "null")
        _row("hours_since_fetch:", f"{state.hours_since_fetch:.1f}" if state.hours_since_fetch is not None else "null")
        _row("unscored_count:", state.unscored_count)
        _row("gaps_computed_at:", state.gaps_computed_at or "null")
        _row("gaps_stale:", state.gaps_stale)
        _row("last_cycle_verdict:", state.last_cycle_verdict or "null")
        _row("last_cycle_at:", state.last_cycle_at or "null")

    # 4. Build plan (rule 28)
    plan = build_plan(state, config)

    # 5. Apply --force overrides
    force_agents = force_agents or []
    overrides: list[str] = []
    for task in plan.tasks:
        if task.agent_name in force_agents and task.skipped:
            task.skipped = False
            task.reason = "forced by operator"
            overrides.append(task.agent_name)

    # 6. Print rendered plan before execution (rule 31)
    _banner("Plan")
    print(plan.render())

    # Print override warning if any
    if overrides:
        print(f"\n  WARNING: Plan manually overridden: {overrides}")

    # 7. Dry-run: exit after printing plan
    if dry_run:
        _banner("Dry Run — Exiting Without Execution")
        _row("Elapsed (s):", f"{(datetime.now(timezone.utc) - cycle_start).total_seconds():.2f}")
        print(f"\n{_SEP}\n")
        return

    # 8. Check for "nothing to do" (rule 6)
    if all(task.skipped for task in plan.tasks):
        _banner("Cycle Summary")
        _row("Outcome:", "nothing_to_do")
        _row("Elapsed (s):", f"{(datetime.now(timezone.utc) - cycle_start).total_seconds():.2f}")
        print(f"\n{_SEP}\n")
        # Write cycle summary row (rule 33)
        storage.log_cycle(
            path=config.db_path,
            agent="orchestrator",
            started_at=cycle_start.isoformat(),
            records_touched=0,
            status="nothing_to_do",
            notes="All agents skipped: nothing to do",
        )
        return

    # 9. Execute tasks in order (rule 30)
    _banner("Agent Runs")
    results: list[AgentResult] = []
    cycle_partial = False

    for task in plan.tasks:
        if task.skipped:
            # Skipped tasks are logged but not executed
            results.append(AgentResult(
                agent=task.agent_name,
                status="skipped",
                records_touched=0,
                notes=task.reason,
            ))
            _result_line(results[-1])
            continue

        # Resolve agent from registry
        try:
            if task.agent_name == "fetcher":
                agent = get_fetcher(config)
            else:
                agent = get_agent(task.agent_name, config)
        except ValueError as exc:
            # Agent not found in registry — treat as failure
            cycle_partial = True
            storage.log_cycle(
                path=config.db_path,
                agent=task.agent_name,
                started_at=datetime.now(timezone.utc).isoformat(),
                records_touched=0,
                status="failed",
                notes=f"Agent '{task.agent_name}' not found in registry: {exc}",
            )
            results.append(AgentResult(
                agent=task.agent_name,
                status="failed",
                records_touched=0,
                notes=f"Agent not found in registry",
            ))
            _result_line(results[-1])
            continue

        # Execute with stop_conditions (rule 29)
        try:
            result = agent.run(config, task.stop_conditions)
            results.append(result)
            _result_line(result)
        except Exception as exc:  # rule 32: one failure does not stop the cycle
            cycle_partial = True
            storage.log_cycle(
                path=config.db_path,
                agent=task.agent_name,
                started_at=datetime.now(timezone.utc).isoformat(),
                records_touched=0,
                status="failed",
                notes=f"Exception: {type(exc).__name__}: {exc}",
            )
            results.append(AgentResult(
                agent=task.agent_name,
                status="failed",
                records_touched=0,
                notes=f"Exception: {type(exc).__name__}: {exc}",
            ))
            _result_line(results[-1])

    # 11. Cycle summary (rule 33)
    _banner("Cycle Summary")
    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()

    # Determine outcome
    if cycle_partial:
        outcome = "partial"
    else:
        outcome = "complete"

    _row("Outcome:", outcome)
    _row("Agents run:", len([r for r in results if not r.status == "skipped"]))
    _row("Agents skipped:", len([r for r in results if r.status == "skipped"]))
    _row("Failed:", len([r for r in results if r.status == "failed"]))
    _row("Elapsed (s):", f"{elapsed:.2f}")

    # Write cycle summary row (rule 33)
    override_note = f" | overrides: {overrides}" if overrides else ""
    summary_notes = (
        f"plan: {[t.agent_name for t in plan.tasks]} | "
        f"ran: {[r.agent for r in results if r.status == 'ok']} | "
        f"skipped: {[r.agent for r in results if r.status == 'skipped']} | "
        f"outcome: {outcome}{override_note}"
    )
    storage.log_cycle(
        path=config.db_path,
        agent="orchestrator",
        started_at=cycle_start.isoformat(),
        records_touched=sum(r.records_touched for r in results if r.status == "ok"),
        status=outcome,
        notes=summary_notes,
    )

    print(f"\n{_SEP}\n")
