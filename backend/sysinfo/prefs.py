"""
prefs.py — Persistent user preferences for ARIA.

Stored in data/aria_prefs.json.
All reads/writes are atomic (write-tmp → rename).

Keys:
    muted          bool    — TTS muted
    tts_voice      str     — Kokoro voice name
    tts_speed      float   — TTS speed multiplier
    theme          str     — "dark" | "light"
    ollama_model   str     — active Ollama model name
"""

import json
import os
import logging
from pathlib import Path
from memory import DATA_DIR

logger = logging.getLogger("aria.prefs")

PREFS_FILE = DATA_DIR / "aria_prefs.json"

DEFAULTS = {
    "muted":        False,
    "tts_voice":    "af_heart",
    "tts_speed":    1.0,
    "theme":        "dark",
    "ollama_model": os.getenv("OLLAMA_MODEL", "mistral"),
}


def _load() -> dict:
    try:
        if PREFS_FILE.exists():
            data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Merge with defaults so new keys always exist
                return {**DEFAULTS, **data}
    except Exception as e:
        logger.warning(f"[Prefs] Load failed: {e}")
    return dict(DEFAULTS)


def _save(prefs: dict):
    tmp = PREFS_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        os.replace(tmp, PREFS_FILE)
    except Exception as e:
        logger.warning(f"[Prefs] Save failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def get_all() -> dict:
    """Return all preferences (merged with defaults)."""
    return _load()


def get(key: str):
    """Return a single preference value."""
    return _load().get(key, DEFAULTS.get(key))


def set_pref(key: str, value) -> dict:
    """Set a single preference and persist. Returns updated prefs."""
    if key not in DEFAULTS:
        raise ValueError(f"Unknown preference key: {key!r}")
    prefs = _load()
    prefs[key] = value
    _save(prefs)
    return prefs


def update(updates: dict) -> dict:
    """Update multiple preferences at once. Returns updated prefs."""
    prefs = _load()
    for k, v in updates.items():
        if k in DEFAULTS:
            prefs[k] = v
        else:
            logger.warning(f"[Prefs] Ignoring unknown key: {k!r}")
    _save(prefs)
    return prefs


def reset() -> dict:
    """Reset all preferences to defaults."""
    _save(dict(DEFAULTS))
    return dict(DEFAULTS)