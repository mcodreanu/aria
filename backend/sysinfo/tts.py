"""
tts.py — Kokoro-ONNX Text-to-Speech engine for ARIA.

Model files are downloaded automatically on first run (~85MB total):
  - kokoro-v1.0.onnx     (~82MB, the neural TTS model)
  - voices-v1.0.bin      (~3MB,  all voice embeddings)

Voice used: af_heart — Kokoro's best-rated female voice (v1.0 default).
Sample rate: 24000 Hz, output: WAV bytes streamed to the browser.
"""

import io
import os
import threading
import urllib.request
import logging
from memory import DATA_DIR

logger = logging.getLogger("aria.tts")

# ── Model file locations ────────────────────────────────────────────────────

MODELS_DIR = DATA_DIR / "kokoro_models"
ONNX_PATH    = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH  = MODELS_DIR / "voices-v1.0.bin"

ONNX_URL     = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL   = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# ── Voice config ────────────────────────────────────────────────────────────
#
# Available female voices (af_ = American Female, bf_ = British Female):
#   af_heart    — warm, natural, top-ranked  ← ARIA uses this
#   af_bella    — slightly softer
#   af_sarah    — clear, neutral
#   af_nicole   — bright
#   af_sky      — lighter
#   bf_emma     — British accent
#   bf_isabella — British accent, warmer
#
ARIA_VOICE  = "af_heart"
ARIA_SPEED  = 1.0          # 0.5 = slow, 1.0 = normal, 1.5 = fast
ARIA_LANG   = "en-us"

# ── Lazy-loaded engine ──────────────────────────────────────────────────────

_kokoro = None
# FIX: Lock prevents two concurrent requests from both seeing _kokoro=None
# and double-loading the 82MB model simultaneously.
_load_lock = threading.Lock()


def _download_if_missing():
    """Download model files if not already present. Called once at startup."""
    MODELS_DIR.mkdir(exist_ok=True)

    for path, url in [(ONNX_PATH, ONNX_URL), (VOICES_PATH, VOICES_URL)]:
        if path.exists():
            logger.info(f"[TTS] Model file present: {path.name}")
            continue

        size_mb = 82 if "onnx" in path.name else 3
        logger.info(f"[TTS] Downloading {path.name} (~{size_mb}MB) — first run only...")

        tmp = str(path) + ".tmp"
        try:
            def _progress(block, block_size, total):
                if total > 0:
                    pct = min(block * block_size * 100 // total, 100)
                    print(f"\r  {path.name}: {pct}%", end="", flush=True)

            urllib.request.urlretrieve(url, tmp, reporthook=_progress)
            print()
            os.replace(tmp, str(path))
            logger.info(f"[TTS] Downloaded {path.name}")
        except Exception as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"Failed to download {path.name}: {e}") from e


def _load_engine():
    """
    Load (or return cached) Kokoro engine.

    FIX: Previously used the GIL as an implicit guard, which is not reliable
    under asyncio's thread-pool executor where multiple OS threads can be
    active simultaneously.  The explicit Lock makes the double-init safe.
    """
    global _kokoro
    # Fast path: already loaded, no lock needed
    if _kokoro is not None:
        return _kokoro

    with _load_lock:
        # Re-check inside the lock — another thread may have loaded it while
        # we were waiting to acquire.
        if _kokoro is not None:
            return _kokoro

        try:
            from kokoro_onnx import Kokoro
            _download_if_missing()
            logger.info("[TTS] Loading Kokoro engine...")
            _kokoro = Kokoro(str(ONNX_PATH), str(VOICES_PATH))
            logger.info(f"[TTS] Kokoro ready — voice: {ARIA_VOICE}")
            return _kokoro
        except ImportError:
            raise RuntimeError(
                "kokoro-onnx is not installed. Run: pip install kokoro-onnx soundfile"
            )


# ── Public API ──────────────────────────────────────────────────────────────

def synthesize(text: str, voice: str = ARIA_VOICE,
               speed: float = ARIA_SPEED) -> bytes:
    """
    Convert text to speech.
    Returns raw WAV bytes ready to stream to the browser.
    Raises RuntimeError if Kokoro is unavailable.
    """
    if not text.strip():
        return b""

    import soundfile as sf

    engine = _load_engine()
    samples, sample_rate = engine.create(
        text,
        voice=voice,
        speed=speed,
        lang=ARIA_LANG,
    )

    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


def is_available() -> bool:
    """Return True if kokoro-onnx is importable (model doesn't need to be loaded yet)."""
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile    # noqa: F401
        return True
    except ImportError:
        return False


def preload():
    """
    Call this at server startup to download models and warm up the engine
    so the first TTS request isn't slow.
    """
    try:
        _load_engine()
    except Exception as e:
        logger.warning(f"[TTS] Preload failed (will retry on first request): {e}")