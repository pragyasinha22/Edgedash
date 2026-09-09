"""
Abuse guards for the ask endpoint.

Per abuse prevention requirements:
- Session rate limiting (10 questions per 10 minutes)
- Input validation (length, empty, control chars, injection patterns)
- Global daily cap from config (default 200)
- All rejections logged to query_log
"""

from __future__ import annotations

import re
import string
from datetime import datetime, timezone, timedelta
from typing import Any

from edgedash import storage
from edgedash.config import load_config


# ---------------------------------------------------------------------------
# Session rate limiting
# ---------------------------------------------------------------------------

_SESSION_LIMIT = 10  # questions per session
_SESSION_WINDOW = timedelta(minutes=10)  # rolling window


class SessionRateLimiter:
    """In-memory session rate limiter (for single-instance deployment)."""
    
    def __init__(self) -> None:
        self._history: list[datetime] = []
    
    def check(self) -> tuple[bool, str | None]:
        """
        Check if the session can ask another question.
        
        Returns:
            (allowed, wait_time_str) where wait_time_str is human-readable
            if not allowed, None if allowed
        """
        now = datetime.now(timezone.utc)
        
        # Remove entries outside the window
        cutoff = now - _SESSION_WINDOW
        self._history = [t for t in self._history if t > cutoff]
        
        if len(self._history) >= _SESSION_LIMIT:
            # Calculate wait time until oldest entry expires
            oldest = self._history[0]
            wait_seconds = (oldest + _SESSION_WINDOW - now).total_seconds()
            wait_minutes = int(wait_seconds / 60) + 1
            return False, f"Please wait {wait_minutes} minute(s) before asking again"
        
        # Record this request
        self._history.append(now)
        return True, None


# Global session limiter instance
_session_limiter = SessionRateLimiter()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_MAX_QUESTION_LENGTH = 300

# Instruction injection patterns to detect
_INJECTION_PATTERNS = [
    r"ignore previous",
    r"ignore all previous",
    r"system prompt",
    r"you are now",
    r"act as",
    r"pretend to be",
    r"forget everything",
    r"disregard",
    r"override",
    r"bypass",
]


def _contains_control_chars(text: str) -> bool:
    """Check if text contains control characters (excluding whitespace)."""
    # Allow normal whitespace, reject other control chars
    allowed_whitespace = {' ', '\t', '\n', '\r'}
    for char in text:
        # if char in string.control_characters and char not in allowed_whitespace:
        if ord(char) < 32 and char not in allowed_whitespace:    
            return True
    return False


def _contains_injection_pattern(text: str) -> bool:
    """Check if text contains instruction injection patterns."""
    text_lower = text.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def validate_input(question: str) -> tuple[bool, str | None]:
    """
    Validate user input before any model call.
    
    Returns:
        (valid, rejection_reason) where rejection_reason is None if valid
    """
    # Check empty or whitespace-only
    if not question or not question.strip():
        return False, "rejected: empty input"
    
    # Check length
    if len(question) > _MAX_QUESTION_LENGTH:
        return False, "rejected: input too long"
    
    # Check control characters
    if _contains_control_chars(question):
        return False, "rejected: suspicious input"
    
    # Check injection patterns
    if _contains_injection_pattern(question):
        return False, "rejected: suspicious input"
    
    return True, None


# ---------------------------------------------------------------------------
# Global daily cap
# ---------------------------------------------------------------------------

_DEFAULT_DAILY_CAP = 200


def check_daily_cap() -> tuple[bool, str | None]:
    """
    Check if the global daily question cap has been exceeded.
    
    Returns:
        (allowed, message) where message is None if allowed
    """
    config = load_config()
    daily_cap = getattr(config, "query_daily_cap", _DEFAULT_DAILY_CAP)
    
    # Get today's date in UTC
    today = datetime.now(timezone.utc).date().isoformat()
    
    # Count questions from today
    # This requires query_log table to exist
    try:
        count = storage.count_queries_today(config.db_path, today)
        if count >= daily_cap:
            return False, "Daily question limit reached. Please try again tomorrow."
    except Exception:
        # If query_log doesn't exist yet, allow the query
        # (this handles first-run scenario)
        pass
    
    return True, None


def log_rejection(question: str, reason: str) -> None:
    """Log a rejected question to query_log."""
    config = load_config()
    storage.log_query(
        path=config.db_path,
        question=question,
        tool_used=None,
        params=None,
        answerable=False,
        reason=reason,
        duration_ms=0,
    )


# ---------------------------------------------------------------------------
# Combined guard check
# ---------------------------------------------------------------------------

def check_question_allowed(question: str) -> tuple[bool, str, str | None]:
    """
    Run all abuse guards on a question.
    
    Returns:
        (allowed, rejection_reason, log_reason)
        - allowed: True if question passes all guards
        - rejection_reason: User-facing message (if not allowed)
        - log_reason: Internal log reason (always set if not allowed)
    """
    # 1. Check session rate limit
    session_allowed, session_msg = _session_limiter.check()
    if not session_allowed:
        log_rejection(question, "rejected: session rate limit")
        return False, session_msg, "rejected: session rate limit"
    
    # 2. Check input validation
    input_valid, input_reason = validate_input(question)
    if not input_valid:
        log_rejection(question, input_reason)
        return False, "I couldn't understand that question. Please try asking in a different way.", input_reason
    
    # 3. Check global daily cap
    daily_allowed, daily_msg = check_daily_cap()
    if not daily_allowed:
        log_rejection(question, "rejected: daily cap exceeded")
        return False, daily_msg, "rejected: daily cap exceeded"
    
    return True, None, None
