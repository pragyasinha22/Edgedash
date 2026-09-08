"""
Single gateway for all LLM calls in EdgeDash.

Public API
----------
complete_json(prompt, schema) -> dict
    Send *prompt* to the configured provider, validate the JSON response
    against *schema*, and return the parsed dict.

    Rules enforced here (steering rules 15-17):
    - Rate limit: >= 1 s between calls, rolling cap of 15 calls/minute.
    - Provider/model come from config, never hardcoded.
    - Response validated against schema; one retry on failure; then LLMError.
    - Markdown fences and prose stripped before json.loads.
    - 429 / quota errors trigger exponential backoff (3 attempts), then LLMError.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from typing import Any

from edgedash.config import load_config
from edgedash.settings import require

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception — callers must handle explicitly (rule 17)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Raised when the LLM call or response validation fails unrecoverably."""


# ---------------------------------------------------------------------------
# Rate-limit state (module-level, simple and cheap)
# ---------------------------------------------------------------------------

_last_call_ts: float = 0.0          # wall-clock time of the last outbound call
_call_timestamps: deque[float] = deque()  # timestamps of calls in the last 60 s


def _enforce_rate_limit(rps: int, rpm: int) -> None:
    """Block until sending now would not violate rps or rpm limits."""
    global _last_call_ts

    now = time.monotonic()

    # --- rolling-window rpm cap ---
    cutoff = now - 60.0
    while _call_timestamps and _call_timestamps[0] < cutoff:
        _call_timestamps.popleft()

    if len(_call_timestamps) >= rpm:
        oldest = _call_timestamps[0]
        wait = (oldest + 60.0) - now
        if wait > 0:
            logger.debug("Rate limit (rpm=%d): sleeping %.2f s", rpm, wait)
            time.sleep(wait)

    # --- per-call rps floor ---
    now = time.monotonic()
    gap = now - _last_call_ts
    min_gap = 1.0 / rps
    if gap < min_gap:
        logger.debug("Rate limit (rps=%d): sleeping %.2f s", rps, min_gap - gap)
        time.sleep(min_gap - gap)

    _last_call_ts = time.monotonic()
    _call_timestamps.append(_last_call_ts)


# ---------------------------------------------------------------------------
# Response cleaning
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?(.*?)```", re.DOTALL)


def _strip_to_json(text: str) -> str:
    """Remove markdown fences and any surrounding prose; return bare JSON."""
    # prefer content inside a code fence if one exists
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()

    # otherwise find the first { or [ and take everything from there
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]

    return text.strip()


# ---------------------------------------------------------------------------
# Schema validation (lightweight; no extra dependency)
# ---------------------------------------------------------------------------

def _validate(data: Any, schema: dict) -> None:
    """Minimal JSON-schema-style validation (type + required keys only)."""
    if schema.get("type") == "object":
        if not isinstance(data, dict):
            raise ValueError(f"Expected object, got {type(data).__name__}")
        for key in schema.get("required", []):
            if key not in data:
                raise ValueError(f"Missing required key: '{key}'")
    elif schema.get("type") == "array":
        if not isinstance(data, list):
            raise ValueError(f"Expected array, got {type(data).__name__}")


# ---------------------------------------------------------------------------
# Provider back-ends
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, model: str) -> str:
    """Call Google Gemini using the current google-genai SDK."""
    try:
        from google import genai
    except ImportError as exc:
        raise LLMError(
            "The Google GenAI SDK is not installed. "
            "Run: pip install google-genai"
        ) from exc

    api_key = require("GEMINI_API_KEY")

    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )

        text = response.text

        if not text:
            raise LLMError("Gemini returned an empty response.")

        return text

    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"Gemini API call failed: {exc}") from exc


def _call_ollama(prompt: str, model: str) -> str:
    """Call a local Ollama instance and return the raw text response."""
    import urllib.request

    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    return body.get("response", "")


_PROVIDERS: dict[str, Any] = {
    "gemini": _call_gemini,
    "ollama": _call_ollama,
}


def _raw_call(provider: str, model: str, prompt: str) -> str:
    """Dispatch to the right provider; handle 429/quota with backoff."""
    if provider not in _PROVIDERS:
        raise LLMError(
            f"Unknown llm_provider '{provider}'. "
            f"Supported providers: {list(_PROVIDERS.keys())}. "
            "Change llm_provider in config.yaml."
        )

    fn = _PROVIDERS[provider]
    backoff = 5.0
    for attempt in range(1, 4):
        try:
            return fn(prompt, model)
        except Exception as exc:
            msg = str(exc).lower()
            is_quota = any(k in msg for k in ("429", "quota", "resource_exhausted", "rate"))
            if is_quota and attempt < 3:
                logger.warning(
                    "LLM quota/rate error (attempt %d/3); backing off %.0f s: %s",
                    attempt, backoff, exc,
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            raise LLMError(f"LLM call failed after {attempt} attempt(s): {exc}") from exc

    raise LLMError("LLM call failed after 3 attempts (should not reach here)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def complete_json(prompt: str, schema: dict) -> dict:
    """
    Send *prompt* to the configured LLM provider and return a validated dict.

    The function appends a JSON-only instruction to the prompt, strips any
    markdown fences from the response, parses it, and validates the result
    against *schema*.  On validation failure the prompt is retried once with
    a stricter instruction.  A second failure raises LLMError — callers decide
    what to do (rule 17).
    """
    cfg = load_config()
    provider = cfg.llm_provider
    model = cfg.llm_model

    json_instruction = (
        "\n\nRespond with JSON only. "
        "No markdown, no code fences, no prose — raw JSON that matches this schema: "
        f"{json.dumps(schema)}"
    )

    def _attempt(extra_instruction: str = "") -> dict:
        _enforce_rate_limit(cfg.llm_rps, cfg.llm_rpm)
        full_prompt = prompt + json_instruction + extra_instruction
        raw = _raw_call(provider, model, full_prompt)
        cleaned = _strip_to_json(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON parse failed: {exc}. Raw response snippet: {raw[:200]}") from exc
        _validate(parsed, schema)
        return parsed

    try:
        return _attempt()
    except (LLMError, ValueError) as first_err:
        logger.warning("LLM response validation failed (attempt 1): %s — retrying once", first_err)

    try:
        return _attempt(
            extra_instruction=(
                "\n\nPREVIOUS RESPONSE WAS INVALID. "
                "Return ONLY a raw JSON object/array. Zero prose. Zero markdown."
            )
        )
    except (LLMError, ValueError) as second_err:
        raise LLMError(
            f"LLM response failed validation twice. Last error: {second_err}"
        ) from second_err


# ---------------------------------------------------------------------------
# CLI smoke-test:  python -m edgedash.llm --check
# ---------------------------------------------------------------------------

def _check() -> None:
    cfg = load_config()
    print(f"Provider : {cfg.llm_provider}")
    print(f"Model    : {cfg.llm_model}")
    print("Sending test prompt…")

    schema = {"type": "object", "required": ["ok"]}
    result = complete_json('Return {"ok": true}', schema)
    print(f"Response : {result}")
    print("✓ LLM check passed")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if "--check" in sys.argv:
        _check()
    else:
        print("Usage: python -m edgedash.llm --check")
        sys.exit(1)
