"""
Local speech-to-text helpers for ARIA.

The module is intentionally optional: ARIA can boot without an STT package, and
the /stt endpoint returns setup guidance until one is installed.
"""

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


STT_MODEL = os.getenv("ARIA_STT_MODEL", "tiny.en")
VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor"
_import_errors: dict[str, str] = {}


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        _import_errors.pop(name, None)
        return True
    except Exception as exc:
        first_error = str(exc)

    if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
        sys.path.append(str(VENDOR_DIR))
        try:
            __import__(name)
            _import_errors.pop(name, None)
            return True
        except Exception as exc:
            _import_errors[name] = f"{first_error}; vendor fallback: {exc}"
            return False

    _import_errors[name] = first_error
    return False


def engine_name() -> str | None:
    if _has_module("faster_whisper"):
        return "faster-whisper"
    if _has_module("whisper"):
        return "openai-whisper"
    return None


def is_available() -> bool:
    return engine_name() is not None


def install_hint() -> str:
    detail = ""
    if _import_errors:
        errors = "; ".join(f"{name}: {err}" for name, err in _import_errors.items())
        detail = f" Import errors: {errors}."
    return (
        "Install local speech recognition with: "
        "pip install faster-whisper. The first transcription downloads the "
        f"{STT_MODEL!r} model unless it is already cached."
        f"{detail}"
    )


@lru_cache(maxsize=1)
def _faster_whisper_model():
    from faster_whisper import WhisperModel

    device = os.getenv("ARIA_STT_DEVICE", "cpu")
    compute_type = os.getenv("ARIA_STT_COMPUTE_TYPE", "int8")
    return WhisperModel(STT_MODEL, device=device, compute_type=compute_type)


@lru_cache(maxsize=1)
def _openai_whisper_model():
    import whisper

    return whisper.load_model(STT_MODEL)


def transcribe_bytes(data: bytes, suffix: str = ".webm") -> str:
    if not data:
        return ""
    if not is_available():
        raise RuntimeError(install_hint())

    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        if engine_name() == "faster-whisper":
            segments, _info = _faster_whisper_model().transcribe(str(tmp_path))
            return " ".join(seg.text.strip() for seg in segments).strip()

        result = _openai_whisper_model().transcribe(str(tmp_path))
        return str(result.get("text", "")).strip()
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
