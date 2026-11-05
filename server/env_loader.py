"""Loads optional environment variables from the project's .env file so the user
does not have to export them manually (Windows-friendly: no shell rc edits).

Recognized keys (see config/env.example):
  YOUTUBE_API_KEY     optional: enables the real YouTube Data API search
  OPENAI_API_KEY      optional: enables the ChatGPT mode answers
  MOOD_TOKEN          optional: protects the FastAPI endpoints if it is set
  MOOD_CDP_PORT       optional: debug port for the Brave control (default 9222)
  MOOD_PAGE_PORT      optional: port of the panel page server (default 8642)
"""
from __future__ import annotations

import os
from pathlib import Path

_KNOWN = {"YOUTUBE_API_KEY", "OPENAI_API_KEY", "MOOD_TOKEN", "MOOD_CDP_PORT", "MOOD_PAGE_PORT"}


def load_env(root: Path | None = None) -> dict[str, str]:
    root = root or Path(__file__).resolve().parent.parent
    env_file = root / ".env"
    loaded: dict[str, str] = {}
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in _KNOWN and key not in os.environ:
                os.environ[key] = value
                loaded[key] = value
    return loaded
