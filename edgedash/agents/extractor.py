"""
Extractor agent — pulls structured facts out of a job listing using the LLM.

Rule 16: this module extracts facts ONLY. No scores, no rankings, no weights.
Rule 17: every response is validated; one retry; then failure logged per listing.
Rule 18: idempotent — cache hit returns immediately with no model call.

Public function
---------------
extract(listing: dict) -> dict
    Returns a dict with keys:
        required_skills : list[str]   – explicitly required skills (lowercase)
        nice_to_have    : list[str]   – preferred/bonus skills (lowercase)
        seniority       : str         – "junior"|"mid"|"senior"|"lead"|"unknown"
        remote_ok       : bool | None – True/False if stated, None if not mentioned
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from edgedash import storage
from edgedash.config import load_config
from edgedash.llm import LLMError, complete_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction schema — exactly the fields we need, no score field (rule 16)
# ---------------------------------------------------------------------------

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["required_skills", "nice_to_have", "seniority", "remote_ok"],
    "properties": {
        "required_skills": {"type": "array"},
        "nice_to_have":    {"type": "array"},
        "seniority":       {"type": "string"},
        "remote_ok":       {},           # bool | null — no type constraint needed
    },
}

_VALID_SENIORITY = {"junior", "mid", "senior", "lead", "unknown"}

# Common soft skills and non-technical items to filter out
_SOFT_SKILLS = {
    "communication", "teamwork", "leadership", "problem-solving",
    "analytical skills", "critical thinking", "collaboration",
    "time management", "organization", "adaptability",
    "creativity", "innovation", "attention to detail",
    "proactive", "self-motivated", "detail-oriented",
    "fast-paced environment", "collaborative style", "working style",
    "stakeholders", "written communication", "verbal communication",
    "english", "german", "french", "spanish", "chinese",
    "user-friendliness", "visual design", "structured approach",
    "pragmatism", "ownership", "self-management",
    "quality awareness", "working in a fast-paced environment",
    "collaborative working style", "working with technical stakeholders",
    "working with non-technical stakeholders",
}

# Patterns that indicate non-technical content
_NON_TECH_PATTERNS = [
    "working with", "working in", "ability to", "strong",
    "excellent", "good", "demonstrated", "proven",
]


def _is_technical_skill(skill: str) -> bool:
    """Return True if the skill appears to be a technical skill."""
    skill_lower = skill.lower().strip()
    
    # Filter out known soft skills
    if skill_lower in _SOFT_SKILLS:
        return False
    
    # Filter out phrases that indicate soft skills
    for pattern in _NON_TECH_PATTERNS:
        if pattern in skill_lower:
            return False
    
    # Filter out full sentences (contains spaces and common words)
    words = skill_lower.split()
    if len(words) > 5:  # Likely a sentence or phrase
        return False
    
    # Filter if it contains common soft skill words
    soft_skill_words = {"skills", "ability", "approach", "style", "awareness"}
    if any(word in soft_skill_words for word in words):
        return False
    
    return True

# ---------------------------------------------------------------------------
# Prompt builder — model never sees candidate profile (rule 16)
# ---------------------------------------------------------------------------

def _build_prompt(listing: dict) -> str:
    title = listing.get("title") or "Unknown title"
    company = listing.get("company") or "Unknown company"
    description = listing.get("description") or ""

    return f"""You are a document parser. Read the job listing below and extract structured facts.

Rules:
- Extract ONLY what is explicitly stated in the listing text.
- Do NOT infer, guess, or evaluate anything.
- Do NOT mention or consider any candidate or their profile.
- If the listing does not state something, use null or an empty list.
- There is no candidate. You are reading a document, nothing more.

IMPORTANT: For skills, extract ONLY technical skills:
- Programming languages (Python, Java, SQL, etc.)
- Frameworks and libraries (React, TensorFlow, Pandas, etc.)
- Tools and platforms (Docker, Kubernetes, AWS, Azure, etc.)
- Databases (PostgreSQL, MongoDB, Redis, etc.)
- Technical methodologies (CI/CD, DevOps, Agile/Scrum as technical practices)

DO NOT extract:
- Soft skills (communication, teamwork, leadership, problem-solving)
- Personality traits (proactive, detail-oriented, self-motivated)
- General work habits (fast-paced environment, collaborative style)
- Full sentences or phrases
- Languages (English, German) unless specifically required as a technical skill

Extract these fields:
  required_skills  - TECHNICAL skills the listing explicitly states as required (list of strings, lowercase)
  nice_to_have     - TECHNICAL skills the listing states as preferred or nice-to-have (list of strings, lowercase)
  seniority        - one of: "junior", "mid", "senior", "lead", "unknown" — only if the listing
                     states it clearly; otherwise "unknown"
  remote_ok        - true if the listing explicitly states remote is allowed,
                     false if it explicitly states on-site only,
                     null if not stated

--- JOB LISTING ---
{title} at {company}

{description}
--- END OF LISTING ---"""


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(raw: dict) -> dict:
    """Lowercase skill names; filter non-technical skills; coerce seniority to a valid value."""
    required = [s.lower().strip() for s in (raw.get("required_skills") or []) if s and _is_technical_skill(s)]
    nice = [s.lower().strip() for s in (raw.get("nice_to_have") or []) if s and _is_technical_skill(s)]

    seniority = (raw.get("seniority") or "unknown").lower().strip()
    if seniority not in _VALID_SENIORITY:
        seniority = "unknown"

    remote_raw = raw.get("remote_ok")
    if isinstance(remote_raw, bool):
        remote_ok: bool | None = remote_raw
    elif remote_raw is None:
        remote_ok = None
    else:
        # model occasionally returns 0/1 or "true"/"false"
        remote_ok = bool(remote_raw)

    return {
        "required_skills": required,
        "nice_to_have": nice,
        "seniority": seniority,
        "remote_ok": remote_ok,
    }


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

def _description_hash(listing: dict) -> str:
    """Stable SHA-256 of the job description text."""
    text = (listing.get("description") or "").encode("utf-8")
    return hashlib.sha256(text).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(listing: dict) -> dict:
    """
    Extract structured facts from a job listing dict.

    Checks the extraction cache first (rule 18). On a hit, returns immediately
    with no model call. On a miss, calls the LLM, normalises the result,
    stores it in the cache, and returns it.

    Raises LLMError if both LLM attempts fail — callers log it per listing.
    """
    cfg = load_config()
    desc_hash = _description_hash(listing)

    # --- cache hit ---
    cached = storage.get_extraction(cfg.db_path, desc_hash)
    if cached is not None:
        logger.debug("Extraction cache hit for hash %s", desc_hash[:12])
        return cached

    # --- cache miss: call LLM ---
    prompt = _build_prompt(listing)
    raw = complete_json(prompt, EXTRACTION_SCHEMA)   # raises LLMError on double failure
    result = _normalise(raw)

    storage.save_extraction(cfg.db_path, desc_hash, result)
    logger.debug("Extraction cached for hash %s", desc_hash[:12])

    return result
