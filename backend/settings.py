"""Central runtime settings for ARIA."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    values = [v.strip() for v in raw.split(",") if v.strip()]
    return values or default


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "").strip()
OLLAMA_CONNECT_TIMEOUT = _float_env("OLLAMA_CONNECT_TIMEOUT", 3.0)
OLLAMA_GENERATE_TIMEOUT = _float_env("OLLAMA_GENERATE_TIMEOUT", 60.0)

SESSION_TTL_SECONDS = _int_env("ARIA_SESSION_TTL_SECONDS", 3600)
MAX_SESSIONS = _int_env("ARIA_MAX_SESSIONS", 200)

CORS_ORIGINS = _csv_env(
    "ARIA_CORS_ORIGINS",
    ["http://localhost:8000", "http://127.0.0.1:8000"],
)

WORKSPACE_ROOT = Path(
    os.getenv("ARIA_WORKSPACE_ROOT", str(DATA_DIR / "workspace"))
).expanduser()
ALLOW_ABSOLUTE_WORKSPACE_PATHS = os.getenv("ARIA_ALLOW_ABSOLUTE_PATHS", "0") == "1"
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = DATA_DIR / "aria_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_MAX_BYTES = _int_env("ARIA_UPLOAD_MAX_BYTES", 20 * 1024 * 1024)
UPLOAD_MAX_TEXT_CHARS = _int_env("ARIA_UPLOAD_MAX_TEXT_CHARS", 128 * 1024)
UPLOAD_MAX_IMAGE_BYTES = _int_env("ARIA_UPLOAD_MAX_IMAGE_BYTES", 4 * 1024 * 1024)
UPLOAD_MAX_TRANSFORM_CHARS = _int_env("ARIA_UPLOAD_MAX_TRANSFORM_CHARS", 256 * 1024)

TTS_VOICE = os.getenv("ARIA_TTS_VOICE", "af_heart")
TTS_SPEED = _float_env("ARIA_TTS_SPEED", 1.0)
TTS_CACHE_MAX_ITEMS = _int_env("ARIA_TTS_CACHE_MAX_ITEMS", 64)

HEALTH_CACHE_SECONDS = _float_env("ARIA_HEALTH_CACHE_SECONDS", 5.0)
