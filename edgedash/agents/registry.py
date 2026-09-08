
"""
Agent registry — every agent registers itself with @register_agent.

The orchestrator/planner uses stable lowercase agent names:
    fetcher
    scorer
    gap_analyzer

Class display names can remain human-readable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edgedash.agents.base import Agent


# Registry: maps canonical agent name -> agent class
_AGENTS: dict[str, type] = {}


def _canonical_name(name: str) -> str:
    """Convert agent names to one stable registry format."""
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def register_agent(cls: type) -> type:
    """Register an agent under its canonical name."""
    name = getattr(cls, "name", None)

    if not name:
        raise ValueError(
            f"Agent class {cls.__name__} must define a non-empty 'name'."
        )

    _AGENTS[_canonical_name(name)] = cls
    return cls


def get_agent(name: str, config) -> "Agent":
    """Return an agent instance by canonical name."""
    canonical_name = _canonical_name(name)

    if canonical_name not in _AGENTS:
        raise ValueError(
            f"Unknown agent: {name}. "
            f"Registered agents: {sorted(_AGENTS.keys())}"
        )

    return _AGENTS[canonical_name](config)


def list_agents() -> list[str]:
    """Return registered canonical agent names."""
    return sorted(_AGENTS.keys())


def get_fetcher(config) -> "Agent":
    """Return MockFetcher when enabled, otherwise the real Fetcher."""
    if config.use_mock_fetcher:
        return get_agent("mock_fetcher", config)

    return get_agent("fetcher", config)
