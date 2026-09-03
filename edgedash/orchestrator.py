"""
Orchestrator — reads state, decides what to run, delegates to agents, logs everything.
Agents are registered in AGENT_REGISTRY; swap one line to replace a placeholder.
"""

from __future__ import annotations

from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.config import Config

# ---------------------------------------------------------------------------
# Agent registry — replace MockFetcher with RealFetcher in one line (week 2).
# Scorer and GapAnalyzer are stubs until their modules are built.
# ---------------------------------------------------------------------------

class _NotImplementedAgent:
    """Placeholder that logs a skip and does nothing else."""

    def __init__(self, agent_name: str) -> None:
        self.name = agent_name

    def run(self, config: Config) -> AgentResult:
        msg = f"{self.name}: not implemented yet — skipping."
        storage.log_cycle(
            path=config.db_path,
            agent=self.name,
            started_at=datetime.now(timezone.utc).isoformat(),
            records_touched=0,
            status="skipped",
            notes=msg,
        )
        return AgentResult(
            agent=self.name,
            status="skipped",
            records_touched=0,
            notes=msg,
        )


def _make_fetcher(config: Config) -> Agent:
    """Return MockFetcher for offline dev, real Fetcher otherwise."""
    if config.use_mock_fetcher:
        return MockFetcher()
    return Fetcher()


def _build_registry(config: Config) -> list[Agent]:
    return [
        _make_fetcher(config),   # one line controls mock vs real
        Scorer(),
        GapAnalyzer(),
    ]


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

def run_cycle(config: Config) -> None:
    cycle_start = datetime.now(timezone.utc)

    # 1. Init DB
    storage.init_db(config.db_path)

    # 2. Read state
    last_fetch = storage.last_fetch_time(config.db_path)
    unscored   = storage.count_unscored(config.db_path)

    _banner("EdgeDash — Cycle Starting")
    _row("Time (UTC):",    cycle_start.isoformat(timespec="seconds"))
    _row("DB path:",       config.db_path)
    _row("Target role:",   config.target_role)
    _row("Target city:",   config.target_city)
    _row("Last fetch:",    last_fetch or "never")
    _row("Unscored rows:", unscored)
    _row("Mode:",          "MOCK (offline)" if config.use_mock_fetcher else "LIVE")

    # 3. Build registry (config drives mock vs real)
    agent_registry = _build_registry(config)

    # 4. Decide plan
    _banner("Plan")
    for agent in agent_registry:
        print(f"  • {agent.name:<18} → RUN")
    if unscored > 0:
        print(f"  • Scorer will score {unscored} unscored listing(s)")

    # 5. Run agents
    _banner("Agent Runs")
    results: list[AgentResult] = []
    for agent in agent_registry:
        result = agent.run(config)
        results.append(result)
        _result_line(result)

    # 6. Cycle summary
    _banner("Cycle Summary")
    total_new  = sum(r.records_touched for r in results if r.status == "ok")
    ok_count   = sum(1 for r in results if r.status == "ok")
    fail_count = sum(1 for r in results if r.status == "failed")
    elapsed    = (datetime.now(timezone.utc) - cycle_start).total_seconds()

    _row("Agents run:",       len(results))
    _row("  ok:",             ok_count)
    _row("  failed:",         fail_count)
    _row("New listings:",     next((r.records_touched for r in results if r.agent == "Fetcher"), 0))
    _row("Scored:",           next((r.records_touched for r in results if r.agent == "Scorer"), 0))
    _row("Gap-analyzed:",     next((r.records_touched for r in results if r.agent == "GapAnalyzer"), 0))
    _row("Unscored now:",     storage.count_unscored(config.db_path))
    _row("Elapsed (s):",      f"{elapsed:.2f}")
    print(f"\n{_SEP}\n")
