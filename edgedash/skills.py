"""
Skill canonicalisation — pure, deterministic, no I/O, no model.

Public API
----------
canonical(raw, aliases) -> str
    Normalise a raw skill string to its canonical form using the alias map
    loaded from config.yaml.

    Steps (in order):
      1. Lowercase.
      2. Strip surrounding whitespace and punctuation (commas, periods, etc.).
      3. Strip parenthesised suffixes like "(eks)" or "(v3)" — version/flavour
         annotations that prevent alias matching.
      4. Collapse internal whitespace runs to a single space.
      5. Normalise common separator noise: "ci / cd" → "ci/cd".
      6. Look up the result in the alias map. Return the canonical form if
         found, otherwise return the normalised string as-is.

    Same input always produces the same output. No network. No model.

CLI
---
    python -m edgedash.skills --audit

    Reads every required_skills value from the extraction cache in the
    database and prints:
      - The 40 most common raw strings with counts and their canonical form.
      - Raw strings seen exactly once (likely typos, junk, or full sentences).
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from typing import Any

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Matches a parenthesised group anywhere in the string, e.g. " (eks)", "(v2)"
_PARENS_RE = re.compile(r"\s*\([^)]*\)")

# Collapses "ci / cd", "ci- cd", "ci cd" → "ci/cd"  (rule 23 — separator noise)
_SEPARATOR_RE = re.compile(r"\s*[/\-]\s*")


def _normalise(raw: str) -> str:
    """Apply steps 1-5: lowercase, strip, drop parens, collapse whitespace, fix separators."""
    s = raw.lower()
    s = s.strip(" \t\n\r.,;:")          # surrounding punctuation
    s = _PARENS_RE.sub("", s)           # drop "(eks)", "(aws)", etc.
    s = s.strip()
    s = re.sub(r"\s+", " ", s)          # collapse internal whitespace
    # Normalise separator sequences so "ci / cd" and "ci/cd" both become "ci/cd"
    # Only apply inside tokens that already contain / or -
    s = _SEPARATOR_RE.sub("/", s) if ("/" in s or re.search(r"\s-\s", s)) else s
    return s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonical(raw: str, aliases: dict[str, str]) -> str:
    """
    Return the canonical form of *raw* using *aliases*.

    *aliases* maps normalised raw strings → canonical strings.
    It comes from config.yaml → skill_aliases, already lowercased by load_config.

    Returns an empty string for empty/whitespace-only input.
    """
    if not raw or not raw.strip():
        return ""
    normalised = _normalise(raw)
    return aliases.get(normalised, normalised)


# ---------------------------------------------------------------------------
# Audit CLI  —  python -m edgedash.skills --audit
# ---------------------------------------------------------------------------

def _load_all_raw_skills(db_path: str) -> list[str]:
    """Pull every required_skills list out of the extraction cache."""
    import json
    import sqlite3

    # Import via storage would be circular — we reach into sqlite directly here
    # because this is a read-only audit tool, not production code.
    # The one-file rule (rule 2) still holds for write paths.
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT required_skills FROM extraction_cache").fetchall()
    conn.close()

    skills: list[str] = []
    for (cell,) in rows:
        if cell:
            try:
                skills.extend(json.loads(cell))
            except (ValueError, TypeError):
                pass
    return skills


def _audit(db_path: str, aliases: dict[str, str]) -> None:
    skills = _load_all_raw_skills(db_path)
    if not skills:
        print("No extracted skills found in the database yet.")
        return

    counts: Counter[str] = Counter(skills)
    total_unique = len(counts)

    # ── Top 40 ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  TOP 40 RAW SKILL STRINGS  (of {total_unique} unique, {len(skills)} total)")
    print(f"{'─'*60}")
    print(f"  {'COUNT':>6}  {'RAW STRING':<35}  CANONICAL")
    print(f"  {'─'*6}  {'─'*35}  {'─'*20}")
    for raw, count in counts.most_common(40):
        canon = canonical(raw, aliases)
        marker = "" if canon == _normalise(raw) else "  ←"
        print(f"  {count:>6}  {raw:<35}  {canon}{marker}")

    # ── Singletons ──────────────────────────────────────────────────────────
    singletons = sorted(r for r, c in counts.items() if c == 1)
    print(f"\n{'─'*60}")
    print(f"  SEEN EXACTLY ONCE  ({len(singletons)} strings)")
    print(f"  (typos, junk, or full sentences the extractor mis-captured)")
    print(f"{'─'*60}")
    for raw in singletons:
        print(f"  {raw}")

    print(f"\n{'─'*60}\n")


def _suggest_aliases(db_path: str, aliases: dict[str, str]) -> None:
    """
    ONE model call that proposes alias groupings for unmapped skill strings.
    Prints ready-to-paste YAML. Writes nothing. (Rule 23: model suggests, human decides.)
    """
    from edgedash.llm import LLMError, complete_json

    raw_skills = _load_all_raw_skills(db_path)
    if not raw_skills:
        print("No extracted skills found in the database yet.")
        return

    counts: Counter[str] = Counter(raw_skills)

    # Only strings that are NOT already covered by the alias map
    # (i.e. their normalised form is not a key, and the string itself
    #  doesn't already resolve to something different via an alias)
    unmapped: list[tuple[str, int]] = []
    for raw, count in counts.most_common():
        norm = _normalise(raw)
        if norm not in aliases:
            unmapped.append((norm, count))

    if not unmapped:
        print("All skill strings are already covered by your alias map. Nothing to suggest.")
        return

    # Cap at 150 strings to stay within a sensible prompt size
    to_send = unmapped[:150]
    skill_list_str = "\n".join(f"  {s}  (n={c})" for s, c in to_send)

    prompt = f"""You are a skill taxonomy assistant. Below is a list of skill strings extracted
from job listings. Each string has already been lowercased and normalised.

Your job: identify groups of strings that refer to the SAME underlying skill and
propose a canonical name for each group.

STRICT RULES:
- Only group strings you are highly confident refer to the exact same skill.
- When in doubt, do NOT group — leave strings separate.
- Do NOT group skills that are related but distinct (e.g. "spark" and "hadoop").
- Do NOT group technologies with their ecosystems (e.g. "react" and "javascript").
- "node" and "javascript" are DIFFERENT skills — never group them.
- Return ONLY groupings with 2 or more variants.
- For each group, assign confidence "high" only if the strings are unambiguous
  aliases (e.g. "postgres" / "postgresql"). Use "low" for anything debatable.

Skill strings to analyse:
{skill_list_str}"""

    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["canonical", "variants", "confidence"],
        },
    }

    print("\n  Sending ONE model call to suggest alias groupings…\n")

    try:
        proposals = complete_json(prompt, schema)
    except LLMError as exc:
        print(f"  LLM call failed: {exc}")
        sys.exit(1)

    if not proposals:
        print("  Model returned no grouping proposals.")
        return

    # ── Conflict detection ───────────────────────────────────────────────────
    # Find proposals where variants are already separate canonical targets
    # in the existing alias map (i.e. the user has explicitly distinguished them)
    existing_canonicals: set[str] = set(aliases.values())

    # ── Print warning ────────────────────────────────────────────────────────
    sep = "─" * 70
    print(sep)
    print("  ⚠  THESE ARE SUGGESTIONS ONLY — NOTHING HAS BEEN WRITTEN.")
    print("  Review each group carefully before pasting into config.yaml.")
    print("  Merging distinct skills is WORSE than leaving them separate.")
    print("  If a group looks wrong, ignore it. The model over-merges.")
    print(sep)

    # ── Print YAML ───────────────────────────────────────────────────────────
    print("\n  # --- READY-TO-PASTE into the skill_aliases section of config.yaml ---\n")

    has_conflict = False
    for group in proposals:
        canonical_name = str(group.get("canonical", "")).strip().lower()
        variants       = [str(v).strip().lower() for v in (group.get("variants") or []) if v]
        confidence     = str(group.get("confidence", "low")).lower()

        if len(variants) < 2:
            continue

        # Conflict: any variant is already a canonical target in the alias map
        conflicting = [v for v in variants if v in existing_canonicals and v != canonical_name]
        if conflicting:
            has_conflict = True
            print(f"  # ⛔  CONFLICT: your alias map already treats these as SEPARATE canonicals:")
            for c in conflicting:
                print(f"  #     '{c}' is already a canonical target — do NOT merge it into '{canonical_name}'")

        conf_tag = "  # confidence: HIGH" if confidence == "high" else "  # confidence: low — review carefully"
        print(f"  # group: {canonical_name}{conf_tag}")
        for v in variants:
            if v != canonical_name:
                print(f"  {v}: {canonical_name}")
        print()

    if has_conflict:
        print(f"  # ⛔  Conflicts detected above. Check those groups before pasting.\n")

    print(f"  # --- END OF SUGGESTIONS ---\n")
    print(sep)
    print(f"  {len(proposals)} group(s) proposed from {len(to_send)} unmapped strings.")
    print(f"  Run `python -m edgedash.skills --audit` to see the full raw string list.")
    print(sep + "\n")


def _main() -> None:
    if "--suggest-aliases" in sys.argv:
        from edgedash.config import load_config
        cfg = load_config()
        _suggest_aliases(cfg.db_path, cfg.skill_aliases)
    elif "--audit" in sys.argv:
        from edgedash.config import load_config
        cfg = load_config()
        _audit(cfg.db_path, cfg.skill_aliases)
    else:
        print("Usage:")
        print("  python -m edgedash.skills --audit            # top-40 raw strings + singletons")
        print("  python -m edgedash.skills --suggest-aliases  # model-proposed alias groupings (read-only)")
        sys.exit(1)


if __name__ == "__main__":
    _main()
