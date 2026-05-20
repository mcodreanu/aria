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
import logging
import os
from pathlib import Path
from memory import DATA_DIR
from settings import OLLAMA_MODEL, TTS_SPEED, TTS_VOICE

logger = logging.getLogger("aria.prefs")

PREFS_FILE = DATA_DIR / "aria_prefs.json"

DEFAULTS = {
    "muted":        False,
    "tts_voice":    TTS_VOICE,
    "tts_speed":    TTS_SPEED,
    "theme":        "dark",
    "ollama_model": OLLAMA_MODEL,
    "voice_mode": "wake",
    "wake_enabled": True,
    "wake_phrases": ["hey aria"],
    "stt_model": "tiny.en",
    "stt_silence_threshold": 0.035,
    "stt_command_timeout": 8.0,
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
            if k == "tts_speed":
                try:
                    v = max(0.5, min(2.0, float(v)))
                except (TypeError, ValueError):
                    raise ValueError("tts_speed must be a number")
            if k == "theme" and v not in {"dark", "light"}:
                raise ValueError("theme must be 'dark' or 'light'")
            if k == "voice_mode" and v not in {"wake", "push_to_talk"}:
                raise ValueError("voice_mode must be 'wake' or 'push_to_talk'")
            if k == "wake_enabled":
                v = bool(v)
            if k == "wake_phrases":
                if isinstance(v, str):
                    v = [p.strip() for p in v.split(",") if p.strip()]
                if not isinstance(v, list):
                    raise ValueError("wake_phrases must be a list or comma-separated string")
            if k in {"stt_silence_threshold", "stt_command_timeout"}:
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{k} must be a number")
            prefs[k] = v
        else:
            logger.warning(f"[Prefs] Ignoring unknown key: {k!r}")
    _save(prefs)
    return prefs


def reset() -> dict:
    """Reset all preferences to defaults."""
    _save(dict(DEFAULTS))
    return dict(DEFAULTS)
