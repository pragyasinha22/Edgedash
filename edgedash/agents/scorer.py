"""
Scorer agent — extracts facts then scores every unscored listing deterministically.

Flow per listing:
  1. extractor.extract(listing)  -> facts dict (LLM call, cached)
  2. scoring.score_listing(facts, config) -> {score, components, matched, missing}
  3. scoring.build_reason(...)   -> compact human-readable string (rule 19)
  4. storage.update_listing_score(...)

Rules enforced:
  - Rule 17: per-listing try/except — one failure = one skip, loop continues.
  - Rule 18: only listings WHERE fit_score IS NULL, capped at score_batch_size.
  - Rule 20: distribution (count, min, max, mean, spread) logged to cycle_log.
             Spread < 10 → status "suspect".
  - Rule 21: batch capped at config.score_batch_size (default 25).
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone

from edgedash import storage
from edgedash.agents.base import AgentResult
from edgedash.agents.extractor import extract
from edgedash.agents.registry import register_agent
from edgedash.config import Config
from edgedash.llm import LLMError
from edgedash.scoring import build_reason, score_listing
from edgedash.skills import canonical

logger = logging.getLogger(__name__)

NAME = "Scorer"


def _distribution_notes(scores: list[int], failed: int) -> tuple[str, str]:
    """
    Return (notes_string, status) for cycle_log.
    status is "suspect" if spread < 10, else "ok".
    """
    count = len(scores)
    if count == 0:
        return f"scored 0 · {failed} failed", "ok"

    lo   = min(scores)
    hi   = max(scores)
    mean = statistics.mean(scores)
    spread = hi - lo

    suspect = spread < 10 and count > 1
    status  = "suspect" if suspect else "ok"

    notes = (
        f"scored {count} · range {lo}-{hi} · mean {mean:.0f} · "
        f"spread {'SUSPECT <10' if suspect else 'OK'} · {failed} failed"
    )
    return notes, status


@register_agent
class Scorer:
    name: str = NAME

    def __init__(self, config: Config | None = None) -> None:
        # Config needed for registry compatibility, but not used in Scorer
        pass

    def run(self, config: Config, stop_conditions: dict | None = None) -> AgentResult:
        started = datetime.now(timezone.utc).isoformat()
        started_dt = datetime.now(timezone.utc)

        stop = stop_conditions or {}
        max_items = stop.get("max_items", config.score_batch_size)
        max_seconds = stop.get("max_seconds")

        listings = storage.get_unscored_listings(
            config.db_path, limit=max_items
        )

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

        scores:  list[int] = []
        failed:  int       = 0

        for i, listing in enumerate(listings):
            # Check max_seconds before processing each listing
            if max_seconds is not None:
                elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()
                if elapsed >= max_seconds:
                    logger.info("Scorer: stopped after %d listings due to max_seconds=%d", i, max_seconds)
                    break

            listing_id = listing["id"]
            title      = listing.get("title") or listing_id

            try:
                # Step 1: extract structured facts (cached, rule 18)
                facts = extract(listing)

                # Step 2: deterministic score — no LLM, no randomness (rule 16)
                result = score_listing(facts, config)
                score  = result["score"]
                components = result["components"]

                # Attach matched/missing to facts so build_reason can use them
                facts_with_match = {
                    **facts,
                    "matched_skills": result["matched_skills"],
                    "missing_skills": result["missing_skills"],
                }

                # Step 3: human-readable reason from numbers (rule 19)
                reason = build_reason(components, facts_with_match, config)

                # Step 4: persist
                storage.update_listing_score(
                    path=config.db_path,
                    listing_id=listing_id,
                    fit_score=score,
                    fit_reason=reason,
                    score_components=components,
                )
                scores.append(score)
                logger.info("Scored '%s' → %d  %s", title, score, reason)

            except LLMError as exc:
                failed += 1
                logger.warning("Scorer: LLM failure for listing %s — skipping: %s", listing_id, exc)
                storage.log_cycle(
                    path=config.db_path,
                    agent=NAME,
                    started_at=started,
                    records_touched=0,
                    status="failed",
                    notes=f"LLM failure for listing {listing_id}: {exc}",
                )

            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("Scorer: unexpected error for listing %s — skipping: %s", listing_id, exc)
                storage.log_cycle(
                    path=config.db_path,
                    agent=NAME,
                    started_at=started,
                    records_touched=0,
                    status="failed",
                    notes=f"Unexpected error for listing {listing_id}: {exc}",
                )

        notes, status = _distribution_notes(scores, failed)

        storage.log_cycle(
            path=config.db_path,
            agent=NAME,
            started_at=started,
            records_touched=len(scores),
            status=status,
            notes=notes,
        )

        return AgentResult(
            agent=NAME,
            status=status,
            records_touched=len(scores),
            notes=notes,
        )
