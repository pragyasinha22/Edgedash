"""
Single place that loads environment variables.
All other modules import from here — never from os.environ directly.
"""

from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

# Load .env from repo root if it exists. Safe to call even if the file is absent.
load_dotenv(pathlib.Path(__file__).parent.parent / ".env")


def get(key: str, default: str | None = None) -> str | None:
    """Return an env var value, or default if not set."""
    return os.environ.get(key, default)


def require(key: str) -> str:
    """Return an env var value, raising clearly if it is missing."""
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file (see .env.example)."
        )
    return value
