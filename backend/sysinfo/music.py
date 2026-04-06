"""
music.py — Music playback for ARIA via yt-dlp + FastAPI streaming proxy.

Why a proxy?
  YouTube's direct audio URLs are signed and expire within minutes.
  Giving the browser the raw URL works briefly then breaks with "expired".
  Instead we store the original video URL and proxy the stream server-side:
    browser → GET /music/stream/<id> → server re-extracts fresh URL → pipes bytes
  The browser sees a stable local URL that never expires.
"""

import os
import time
import uuid
import logging
import tempfile
import threading
from typing import Optional
from memory import DATA_DIR

logger = logging.getLogger("aria.music")

MAX_SEARCH_RESULTS   = 3
COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip().lower()

_temp_files: dict[str, tuple[str, float]] = {}
_temp_lock  = threading.Lock()

TEMP_DIR = DATA_DIR / "aria_music"
TEMP_DIR.mkdir(exist_ok=True)
TEMP_FILE_TTL = 3600


class _TrackState:
    def __init__(self):
        self.stream_id:   Optional[str] = None   # UUID key for _stream_registry
        self.title:       Optional[str] = None
        self.webpage_url: Optional[str] = None   # stable YouTube URL for re-extraction
        self.duration:    Optional[int] = None
        self.thumbnail:   Optional[str] = None
        self.playing:     bool = False

    def clear(self):
        self.__init__()


_state = _TrackState()

# Maps stream_id → webpage_url so the proxy endpoint can look it up
_stream_registry: dict[str, str] = {}
_registry_lock = threading.Lock()


def _ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def _build_ydl_opts(extra: dict | None = None) -> dict:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "noplaylist": True,
        "format_sort": ["acodec:opus", "acodec:aac", "acodec:mp3"],
    }
    if COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    if extra:
        opts.update(extra)
    return opts


def _extract_entries(query: str) -> list[dict]:
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(_build_ydl_opts()) as ydl:
            info = ydl.extract_info(f"ytsearch{MAX_SEARCH_RESULTS}:{query}", download=False)
            if not info:
                return []
            entries = info.get("entries")
            return [e for e in entries if e] if entries else [info]
    except Exception as e:
        logger.error(f"[Music] Search failed: {e}")
        return []


def _is_age_restricted(info: dict) -> bool:
    return bool(info.get("age_limit", 0))


def fresh_stream_url(webpage_url: str) -> Optional[str]:
    """
    Re-extract a fresh signed audio URL from a stable YouTube video URL.
    Called by the proxy endpoint on every request so the URL is never stale.
    """
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(_build_ydl_opts()) as ydl:
            info = ydl.extract_info(webpage_url, download=False)
            if not info:
                return None
            return _get_direct_url(info)
    except Exception as e:
        logger.error(f"[Music] Re-extraction failed for {webpage_url}: {e}")
        return None


def _get_direct_url(info: dict) -> Optional[str]:
    formats = info.get("formats", [])
    for codec in ("opus", "aac", "mp3"):
        for fmt in formats:
            if (fmt.get("acodec", "").startswith(codec)
                    and fmt.get("vcodec", "none") == "none"
                    and fmt.get("url")):
                return fmt["url"]
    for fmt in formats:
        if (fmt.get("vcodec", "none") == "none"
                and fmt.get("url")
                and fmt.get("acodec", "none") != "none"):
            return fmt["url"]
    return info.get("url")


def is_available() -> bool:
    return _ytdlp_available()


def search_and_prepare(query: str) -> dict:
    if not _ytdlp_available():
        return {"success": False, "error": "yt-dlp is not installed. Run: pip install yt-dlp"}

    logger.info(f"[Music] Searching: {query!r}")
    entries = _extract_entries(query)

    if not entries:
        return {"success": False, "error": f"No results found for '{query}'. Try a different search."}

    skipped_age = 0
    for info in entries:
        if _is_age_restricted(info):
            skipped_age += 1
            logger.info(f"[Music] Skipping age-restricted: {info.get('title', '?')!r}")
            continue

        # We need the stable webpage URL for proxy re-extraction
        webpage_url = info.get("webpage_url") or info.get("url")
        if not webpage_url or "youtube.com/watch" not in webpage_url:
            logger.warning(f"[Music] No stable webpage_url for: {info.get('title', '?')!r}")
            continue

        # Verify we can extract a URL right now (smoke-test)
        if not _get_direct_url(info):
            logger.warning(f"[Music] No playable format for: {info.get('title', '?')!r}")
            continue

        title     = info.get("title", query)
        duration  = int(info.get("duration", 0) or 0)
        thumbnail = info.get("thumbnail", "")

        # Register for proxy access
        stream_id = str(uuid.uuid4())
        with _registry_lock:
            # Clean up any previous stream
            if _state.stream_id:
                _stream_registry.pop(_state.stream_id, None)
            _stream_registry[stream_id] = webpage_url

        _state.stream_id   = stream_id
        _state.title       = title
        _state.webpage_url = webpage_url
        _state.duration    = duration
        _state.thumbnail   = thumbnail
        _state.playing     = True

        logger.info(f"[Music] Ready: {title!r} ({duration}s) → stream_id={stream_id[:8]}")
        return {
            "success":   True,
            "title":     title,
            "stream_id": stream_id,          # frontend uses /music/stream/<id>
            "duration":  duration,
            "thumbnail": thumbnail,
        }

    if skipped_age == len(entries):
        tip = (
            " Set YTDLP_COOKIES_FROM_BROWSER=chrome in your .env to bypass age restrictions."
            if not COOKIES_FROM_BROWSER else " Try a different search term."
        )
        return {"success": False, "error": f"All results for '{query}' are age-restricted.{tip}"}

    return {"success": False, "error": f"Could not find a playable stream for '{query}'. Try a different search."}


def stop() -> None:
    if _state.stream_id:
        with _registry_lock:
            _stream_registry.pop(_state.stream_id, None)
    _state.clear()
    _cleanup_temp_files()
    logger.info("[Music] Stopped.")


def current_track() -> Optional[dict]:
    if not _state.playing or not _state.title:
        return None
    return {
        "title":     _state.title,
        "duration":  _state.duration,
        "thumbnail": _state.thumbnail,
        "playing":   _state.playing,
    }


def get_webpage_url(stream_id: str) -> Optional[str]:
    with _registry_lock:
        return _stream_registry.get(stream_id)


def register_temp_file(filepath: str) -> str:
    file_id = str(uuid.uuid4())
    with _temp_lock:
        _temp_files[file_id] = (filepath, time.time())
    return file_id


def delete_temp_file(file_id: str) -> None:
    with _temp_lock:
        entry = _temp_files.pop(file_id, None)
    if entry:
        filepath, _ = entry
        try:
            os.remove(filepath)
        except Exception:
            pass


def _cleanup_temp_files() -> None:
    with _temp_lock:
        ids = list(_temp_files.keys())
    for fid in ids:
        delete_temp_file(fid)


def cleanup_stale_temp_files() -> None:
    now = time.time()
    with _temp_lock:
        stale = [fid for fid, (_, ts) in _temp_files.items() if now - ts > TEMP_FILE_TTL]
    for fid in stale:
        delete_temp_file(fid)