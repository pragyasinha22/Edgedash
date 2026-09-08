"""Entry point. Run one full EdgeDash cycle: python run_cycle.py"""

import argparse

from edgedash.config import load_config
from edgedash.orchestrator import run_cycle


def _parse_args():
    parser = argparse.ArgumentParser(description="Run EdgeDash cycle")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read state, build plan, print it, and exit without executing"
    )
    parser.add_argument(
        "--force",
        action="append",
        dest="force_agents",
        help="Force an agent to run even if state says skip (repeatable)"
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print full SystemState with decision explanations"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    config = load_config()
    run_cycle(
        config,
        dry_run=args.dry_run,
        force_agents=args.force_agents or [],
        explain=args.explain,
    )
