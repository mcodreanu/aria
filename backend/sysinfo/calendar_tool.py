"""
calendar_tool.py — Local .ics calendar integration for ARIA.

Reads a local .ics file (iCalendar format) — no account, no OAuth needed.
Supports: listing events, searching, adding simple events.

Configuration (.env):
    ARIA_CALENDAR_PATH = /path/to/calendar.ics

Install dependency:
    pip install icalendar

Usage:
    get_events_today()
    get_events_week()
    get_upcoming(n=5)
    add_event(title, start_dt, end_dt, description="")
    search_events(query)
"""

import os
import logging
from pathlib import Path
from typing import Optional
from memory import DATA_DIR

logger = logging.getLogger("aria.calendar")

# Default calendar file lives in data/ unless overridden by .env
_DEFAULT_CAL = DATA_DIR / "aria_calendar.ics"
CALENDAR_PATH = Path(os.getenv("ARIA_CALENDAR_PATH", str(_DEFAULT_CAL)))


def _ical_available() -> bool:
    try:
        import icalendar  # noqa: F401
        return True
    except ImportError:
        return False


def _not_installed() -> str:
    return (
        "Calendar requires **icalendar**.\n"
        "Install it with: `pip install icalendar` and restart ARIA."
    )


def _ensure_file():
    """Create an empty calendar file if it doesn't exist."""
    if not CALENDAR_PATH.exists():
        CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
        CALENDAR_PATH.write_text(
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//ARIA//Local Calendar//EN\r\n"
            "END:VCALENDAR\r\n",
            encoding="utf-8",
        )


def _load_calendar():
    import icalendar
    _ensure_file()
    with open(CALENDAR_PATH, "rb") as f:
        return icalendar.Calendar.from_ical(f.read())


def _save_calendar(cal):
    _ensure_file()
    with open(CALENDAR_PATH, "wb") as f:
        f.write(cal.to_ical())


def _parse_events(cal) -> list[dict]:
    """Extract and normalise all VEVENT components."""
    import icalendar
    import datetime

    events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        try:
            dtstart = component.get("DTSTART")
            dtend   = component.get("DTEND")
            summary = str(component.get("SUMMARY", "(no title)"))
            desc    = str(component.get("DESCRIPTION", ""))
            uid     = str(component.get("UID", ""))

            if not dtstart:
                continue

            start = dtstart.dt
            end   = dtend.dt if dtend else None

            # Normalise to datetime for easy comparison
            if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime):
                start = datetime.datetime.combine(start, datetime.time.min)
            if isinstance(end, datetime.date) and not isinstance(end, datetime.datetime):
                end = datetime.datetime.combine(end, datetime.time.max)

            # Make timezone-naive for simple comparison
            if hasattr(start, "tzinfo") and start.tzinfo:
                start = start.replace(tzinfo=None)
            if end and hasattr(end, "tzinfo") and end.tzinfo:
                end = end.replace(tzinfo=None)

            events.append({
                "uid":     uid,
                "title":   summary,
                "desc":    desc,
                "start":   start,
                "end":     end,
            })
        except Exception as e:
            logger.warning(f"[Calendar] Skipping malformed event: {e}")

    return sorted(events, key=lambda e: e["start"])


def _fmt_event(ev: dict) -> str:
    start = ev["start"]
    end   = ev["end"]
    title = ev["title"]
    desc  = f"\n    *{ev['desc'][:80]}*" if ev["desc"] else ""
    time_str = start.strftime("%a %b %d, %H:%M")
    if end:
        if end.date() == start.date():
            time_str += f" → {end.strftime('%H:%M')}"
        else:
            time_str += f" → {end.strftime('%b %d %H:%M')}"
    return f"📅 **{title}** — {time_str}{desc}"


# ── Public API ────────────────────────────────────────────────────────────────

def get_events_today() -> str:
    if not _ical_available():
        return _not_installed()
    import datetime
    try:
        cal    = _load_calendar()
        events = _parse_events(cal)
        today  = datetime.date.today()
        todays = [e for e in events if e["start"].date() == today]
        if not todays:
            return f"No events scheduled for today ({today.strftime('%A, %B %d')})."
        lines = [f"**Today's events ({today.strftime('%A, %B %d')}):**"]
        lines += [_fmt_event(e) for e in todays]
        return "\n".join(lines)
    except Exception as ex:
        return f"Calendar error: {ex}"


def get_events_week() -> str:
    if not _ical_available():
        return _not_installed()
    import datetime
    try:
        cal    = _load_calendar()
        events = _parse_events(cal)
        today  = datetime.date.today()
        week_end = today + datetime.timedelta(days=7)
        week_evs = [e for e in events if today <= e["start"].date() <= week_end]
        if not week_evs:
            return "No events in the next 7 days."
        lines = ["**Events this week:**"]
        lines += [_fmt_event(e) for e in week_evs]
        return "\n".join(lines)
    except Exception as ex:
        return f"Calendar error: {ex}"


def get_upcoming(n: int = 5) -> str:
    if not _ical_available():
        return _not_installed()
    import datetime
    try:
        cal    = _load_calendar()
        events = _parse_events(cal)
        now    = datetime.datetime.now()
        upcoming = [e for e in events if e["start"] >= now][:n]
        if not upcoming:
            return "No upcoming events found."
        lines = [f"**Next {len(upcoming)} upcoming events:**"]
        lines += [_fmt_event(e) for e in upcoming]
        return "\n".join(lines)
    except Exception as ex:
        return f"Calendar error: {ex}"


def search_events(query: str) -> str:
    if not _ical_available():
        return _not_installed()
    try:
        cal    = _load_calendar()
        events = _parse_events(cal)
        q      = query.lower()
        found  = [e for e in events
                  if q in e["title"].lower() or q in e["desc"].lower()]
        if not found:
            return f"No events found matching **\"{query}\"**."
        lines = [f"**Calendar search results for \"{query}\":**"]
        lines += [_fmt_event(e) for e in found[:10]]
        return "\n".join(lines)
    except Exception as ex:
        return f"Calendar error: {ex}"


def add_event(title: str, start_str: str,
              end_str: Optional[str] = None,
              description: str = "") -> str:
    if not _ical_available():
        return _not_installed()
    import datetime
    import uuid
    import icalendar

    # Parse start datetime — try several formats
    formats = [
        "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%Y-%m-%d",
    ]
    start_dt = None
    for fmt in formats:
        try:
            start_dt = datetime.datetime.strptime(start_str.strip(), fmt)
            break
        except ValueError:
            continue

    if not start_dt:
        return (
            f"Couldn't parse date **\"{start_str}\"**.\n"
            "Use format: `YYYY-MM-DD HH:MM` e.g. `2025-06-15 14:30`"
        )

    end_dt = None
    if end_str:
        for fmt in formats:
            try:
                end_dt = datetime.datetime.strptime(end_str.strip(), fmt)
                break
            except ValueError:
                continue
    if not end_dt:
        end_dt = start_dt + datetime.timedelta(hours=1)

    try:
        cal   = _load_calendar()
        event = icalendar.Event()
        event.add("SUMMARY",     title)
        event.add("DTSTART",     start_dt)
        event.add("DTEND",       end_dt)
        event.add("DESCRIPTION", description)
        event.add("UID",         str(uuid.uuid4()))
        event.add("DTSTAMP",     datetime.datetime.now())
        cal.add_component(event)
        _save_calendar(cal)
        return (
            f"✅ Event added: **{title}**\n"
            f"📅 {start_dt.strftime('%A, %B %d %Y at %H:%M')} → "
            f"{end_dt.strftime('%H:%M')}"
        )
    except Exception as ex:
        return f"Failed to add event: {ex}"


def get_calendar_path() -> str:
    return str(CALENDAR_PATH)