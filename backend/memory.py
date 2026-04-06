"""
memory.py — ARIA session memory with persistence.

Facts (your name, notes, etc.) are saved to aria_memory.json next to this
file and reloaded on every new connection, so ARIA remembers you across
restarts. Conversation history is session-only (in-RAM) — it would be too
noisy to persist every chat line forever.
"""

import json
import time
import os
from pathlib import Path          # ← was wrongly "from anyio import Path"
from typing import Optional

# DATA_DIR is one level up from backend/ — all runtime files live here.
# mkdir(parents, exist_ok) runs at import time so every module that imports
# DATA_DIR is guaranteed the directory exists before it tries to use it.
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = DATA_DIR / "aria_memory.json"


def _load_facts() -> dict:
    """Read persisted facts from disk. Returns empty dict on any error."""
    try:
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _save_facts(facts: dict) -> None:
    """Write facts dict to disk atomically (write temp → rename)."""
    tmp = MEMORY_FILE.with_suffix(".tmp")   # ← was MEMORY_FILE + ".tmp" which crashes on Path
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(facts, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MEMORY_FILE)
    except Exception:
        pass  # Never crash ARIA over a disk write failure


class Memory:
    """
    Two-tier memory:
      • facts   — persisted to aria_memory.json (name, notes, preferences…)
      • history — in-RAM only, current session conversation log
    """

    def __init__(self, max_entries: int = 50):
        self.max_entries = max_entries
        self.history: list[dict] = []
        self.facts: dict[str, str] = _load_facts()
        self.session_start = time.time()

    # ── Conversation history (session only) ──────────────────────────────────

    def add(self, role: str, text: str):
        self.history.append({"role": role, "text": text, "ts": time.time()})
        if len(self.history) > self.max_entries:
            self.history.pop(0)

    def recent(self, n: int = 6) -> list[dict]:
        return self.history[-n:]

    def last_user_message(self) -> Optional[str]:
        for entry in reversed(self.history):
            if entry["role"] == "user":
                return entry["text"]
        return None

    def full_context(self) -> str:
        return " ".join(e["text"] for e in self.recent(10)).lower()

    # ── Persistent facts ─────────────────────────────────────────────────────

    def remember(self, key: str, value: str):
        self.facts[key] = value
        _save_facts(self.facts)

    def recall(self, key: str) -> Optional[str]:
        return self.facts.get(key)

    def forget(self, key: str):
        self.facts.pop(key, None)
        _save_facts(self.facts)

    def clear_facts(self):
        self.facts.clear()
        _save_facts(self.facts)

    # ── Session info ─────────────────────────────────────────────────────────

    def session_duration(self) -> str:
        elapsed = int(time.time() - self.session_start)
        minutes, seconds = divmod(elapsed, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def reset(self):
        """Clears everything — history AND persisted facts."""
        self.history.clear()
        self.facts.clear()
        self.session_start = time.time()
        _save_facts(self.facts)