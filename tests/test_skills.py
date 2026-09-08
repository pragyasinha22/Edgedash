"""
Tests for edgedash/skills.py — pure function, no I/O, no model.

Covers:
  1. Case normalisation ("PostgreSQL" → "postgres")
  2. Surrounding whitespace stripped ("  python  " → "python")
  3. Parenthesised suffix dropped ("kubernetes (eks)" → "kubernetes")
  4. Aliased term resolved ("postgresql" → "postgres")
  5. Term with no alias returned as-is normalised ("pandas" → "pandas")
  6. Empty string returns empty string (no crash, no KeyError)
  7. Internal whitespace collapsed ("power  bi" → "power bi")
  8. Separator normalisation ("ci / cd" → "ci/cd")
  9. Node stays separate from javascript ("nodejs" → "node", "js" → "javascript")
"""

from __future__ import annotations

import pytest

from edgedash.skills import canonical

# Minimal alias map mirroring the real config.yaml entries used in tests
_ALIASES: dict[str, str] = {
    "postgresql":   "postgres",
    "psql":         "postgres",
    "pg":           "postgres",
    "k8s":          "kubernetes",
    "nodejs":       "node",
    "node.js":      "node",
    "js":           "javascript",
    "ci/cd":        "ci/cd",
    "ci cd":        "ci/cd",
    "cicd":         "ci/cd",
    "ci-cd":        "ci/cd",
    "ml":           "machine learning",
}


# 1. Case normalisation
def test_case_normalisation() -> None:
    assert canonical("PostgreSQL", _ALIASES) == "postgres"
    assert canonical("PYTHON", _ALIASES) == "python"
    assert canonical("SQL", _ALIASES) == "sql"


# 2. Surrounding whitespace
def test_surrounding_whitespace_stripped() -> None:
    assert canonical("  python  ", _ALIASES) == "python"
    assert canonical("\tpandas\n", _ALIASES) == "pandas"


# 3. Parenthesised suffix dropped
def test_parens_suffix_dropped() -> None:
    assert canonical("kubernetes (eks)", _ALIASES) == "kubernetes"
    assert canonical("React (v18)", _ALIASES) == "react"
    assert canonical("AWS (S3, EC2)", _ALIASES) == "aws"


# 4. Aliased term resolved
def test_aliased_term() -> None:
    assert canonical("postgresql", _ALIASES) == "postgres"
    assert canonical("psql", _ALIASES) == "postgres"
    assert canonical("k8s", _ALIASES) == "kubernetes"
    assert canonical("ml", _ALIASES) == "machine learning"


# 5. No alias → returns normalised string as-is
def test_no_alias_passthrough() -> None:
    assert canonical("pandas", _ALIASES) == "pandas"
    assert canonical("tableau", _ALIASES) == "tableau"
    assert canonical("dbt", _ALIASES) == "dbt"


# 6. Empty / whitespace-only input → empty string, no crash
def test_empty_string() -> None:
    assert canonical("", _ALIASES) == ""
    assert canonical("   ", _ALIASES) == ""


# 7. Internal whitespace collapsed
def test_internal_whitespace_collapsed() -> None:
    assert canonical("power  bi", _ALIASES) == "power bi"
    assert canonical("machine   learning", _ALIASES) == "machine learning"


# 8. Separator normalisation
def test_separator_normalisation() -> None:
    assert canonical("ci / cd", _ALIASES) == "ci/cd"
    assert canonical("CI / CD", _ALIASES) == "ci/cd"
    assert canonical("cicd", _ALIASES) == "ci/cd"
    assert canonical("ci-cd", _ALIASES) == "ci/cd"


# 9. Node stays separate from javascript
def test_node_separate_from_javascript() -> None:
    assert canonical("nodejs", _ALIASES) == "node"
    assert canonical("node.js", _ALIASES) == "node"
    assert canonical("js", _ALIASES) == "javascript"
    # plain "node" has no alias — should come through as "node"
    assert canonical("node", _ALIASES) == "node"
    # plain "javascript" has no alias — should come through as "javascript"
    assert canonical("javascript", _ALIASES) == "javascript"
