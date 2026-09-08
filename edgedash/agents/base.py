"""Base contract every EdgeDash agent must satisfy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from edgedash.config import Config


@dataclass
class AgentResult:
    agent: str
    status: str          # "ok" | "failed"
    records_touched: int
    notes: str = ""


class Agent(Protocol):
    """Every agent exposes a name and a run method. Nothing else is required."""

    name: str

    def run(self, config: Config, stop_conditions: dict | None = None) -> AgentResult:
        ...
