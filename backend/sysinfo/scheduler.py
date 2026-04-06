"""
scheduler.py — Task & reminder scheduler for ARIA.

Uses APScheduler (AsyncIOScheduler) so jobs run inside the existing
FastAPI event loop — no extra threads needed.

Reminders survive restarts: they're stored in aria_memory.json under
a "reminders" key and reloaded + rescheduled on startup.

Requirements:
    pip install apscheduler

Usage from aria_brain.py:
    scheduler.add_reminder(session_id, "check the oven", in_minutes=20)
    scheduler.list_reminders() -> list[dict]
    scheduler.cancel_reminder(reminder_id)

When a reminder fires, it calls the registered push_callback(session_id, text)
which main.py wires to send a WebSocket message.
"""

import re
import time
import uuid
import logging
import asyncio
from typing import Callable, Optional

logger = logging.getLogger("aria.scheduler")

# Reminder storage lives in aria_memory.json via the Memory object.
# We keep a module-level in-process copy for fast lookup.
_reminders: dict[str, dict] = {}   # reminder_id → {id, session_id, text, fire_at}

# Registered by main.py at startup
_push_callback: Optional[Callable] = None
_scheduler = None


def init(push_callback: Callable, memory):
    """
    Call once at FastAPI startup.
      push_callback(session_id, message_text) — async fn to send WS notification
      memory — the shared Memory instance for persistence
    """
    global _push_callback, _scheduler, _memory

    _push_callback = push_callback
    _memory = memory

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.start()
        logger.info("[Scheduler] APScheduler started.")
        _reload_from_memory()
    except ImportError:
        logger.warning(
            "[Scheduler] apscheduler not installed — reminders disabled. "
            "Run: pip install apscheduler"
        )


def is_available() -> bool:
    return _scheduler is not None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _save_to_memory():
    if _memory:
        import json
        _memory.remember("_reminders", json.dumps(list(_reminders.values())))


def _reload_from_memory():
    """Re-schedule any reminders that survived a restart and haven't fired yet."""
    if not _memory:
        return
    import json
    raw = _memory.recall("_reminders")
    if not raw:
        return
    try:
        items = json.loads(raw)
        now = time.time()
        for item in items:
            if item.get("fire_at", 0) > now:
                _reminders[item["id"]] = item
                _schedule_job(item)
                logger.info(f"[Scheduler] Restored reminder: {item['text']!r}")
    except Exception as e:
        logger.warning(f"[Scheduler] Could not reload reminders: {e}")


def _schedule_job(reminder: dict):
    """Add the APScheduler one-shot job for a reminder."""
    from apscheduler.triggers.date import DateTrigger
    import datetime

    fire_dt = datetime.datetime.fromtimestamp(reminder["fire_at"])
    _scheduler.add_job(
        _fire_reminder,
        trigger=DateTrigger(run_date=fire_dt),
        args=[reminder["id"]],
        id=reminder["id"],
        replace_existing=True,
        misfire_grace_time=300,   # fire up to 5 min late if server was down
    )


async def _fire_reminder(reminder_id: str):
    reminder = _reminders.pop(reminder_id, None)
    if not reminder:
        return

    _save_to_memory()
    text = f"⏰ **Reminder:** {reminder['text']}"
    logger.info(f"[Scheduler] Firing: {text!r}")

    if _push_callback:
        try:
            await _push_callback(reminder.get("session_id"), text)
        except Exception as e:
            logger.error(f"[Scheduler] Push failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def add_reminder(session_id, text: str,
                 in_minutes: float = 0,
                 in_seconds: float = 0,
                 at_timestamp: float = 0) -> dict:
    """
    Schedule a reminder.

    Provide exactly one of:
        in_minutes=20          — relative delay
        in_seconds=90          — relative delay in seconds
        at_timestamp=1700000000 — absolute Unix timestamp

    Returns the reminder dict (id, text, fire_at).
    """
    if not is_available():
        return {"error": "Reminders unavailable — install apscheduler: pip install apscheduler"}

    if at_timestamp:
        fire_at = at_timestamp
    else:
        delay = (in_minutes * 60) + in_seconds
        if delay <= 0:
            return {"error": "Reminder time must be in the future."}
        fire_at = time.time() + delay

    reminder = {
        "id":         str(uuid.uuid4())[:8],
        "session_id": session_id,
        "text":       text.strip(),
        "fire_at":    fire_at,
    }
    _reminders[reminder["id"]] = reminder
    _schedule_job(reminder)
    _save_to_memory()

    logger.info(f"[Scheduler] Added: {text!r} at {fire_at}")
    return reminder


def list_reminders() -> list[dict]:
    """Return all pending reminders sorted by fire time."""
    now = time.time()
    pending = [r for r in _reminders.values() if r["fire_at"] > now]
    return sorted(pending, key=lambda r: r["fire_at"])


def cancel_reminder(reminder_id: str) -> bool:
    """Cancel a reminder by id. Returns True if found and cancelled."""
    reminder = _reminders.pop(reminder_id, None)
    if not reminder:
        return False
    try:
        _scheduler.remove_job(reminder_id)
    except Exception:
        pass
    _save_to_memory()
    return True


def cancel_all() -> int:
    """Cancel all pending reminders. Returns count cancelled."""
    ids = list(_reminders.keys())
    for rid in ids:
        cancel_reminder(rid)
    return len(ids)


# ── Natural-language time parser ──────────────────────────────────────────────

def parse_reminder_text(text: str) -> Optional[dict]:
    """
    Parse a natural-language reminder string.
    Returns {"message": str, "in_seconds": float} or None.

    Examples:
        "remind me in 20 minutes to check the oven"
        "remind me in 1 hour to call mom"
        "set a reminder for 30 seconds — test"
        "remind me to drink water in 2 hours"
    """
    text_l = text.lower().strip()

    # Patterns: "in X unit" anywhere in the string
    time_patterns = [
        (r"in\s+(\d+(?:\.\d+)?)\s*(?:second|sec|s)\b",    1),
        (r"in\s+(\d+(?:\.\d+)?)\s*(?:minute|min|m)\b",    60),
        (r"in\s+(\d+(?:\.\d+)?)\s*(?:hour|hr|h)\b",       3600),
        (r"in\s+(\d+(?:\.\d+)?)\s*(?:day|d)\b",           86400),
        # "X minutes" without "in" — "remind me 10 minutes ..."
        (r"(\d+(?:\.\d+)?)\s*(?:minute|min)\b",            60),
        (r"(\d+(?:\.\d+)?)\s*(?:hour|hr)\b",               3600),
    ]

    seconds = None
    for pattern, multiplier in time_patterns:
        m = re.search(pattern, text_l)
        if m:
            seconds = float(m.group(1)) * multiplier
            break

    if seconds is None:
        return None

    # Extract the reminder message by stripping the time clause and trigger words
    msg = re.sub(
        r"(?:please\s+)?(?:remind\s+me|set\s+(?:a\s+)?(?:reminder|alarm))\s*",
        "", text_l, flags=re.IGNORECASE
    ).strip()
    msg = re.sub(
        r"(?:in\s+\d+(?:\.\d+)?\s*(?:second|sec|s|minute|min|m|hour|hr|h|day|d)s?\b)",
        "", msg, flags=re.IGNORECASE
    ).strip()
    msg = re.sub(r"^(?:to|for|that|about)\s+", "", msg, flags=re.IGNORECASE).strip()
    msg = re.sub(r"[—\-–]+\s*", "", msg).strip()

    if not msg:
        msg = "reminder"

    # Capitalise first letter
    msg = msg[0].upper() + msg[1:] if msg else msg

    return {"message": msg, "in_seconds": seconds}