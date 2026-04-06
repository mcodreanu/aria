"""
clipboard_tool.py — Clipboard read/write and screenshot for ARIA.

Requirements (all optional — ARIA degrades gracefully without them):
    pip install pyperclip pillow mss

Screenshot flow:
    mss captures the screen → PIL processes → saved to a temp PNG
    The path is returned so the user can open it, or ARIA describes it.

For vision/description of screenshots, pass the image path to Ollama
via a vision-capable model (llava, llama3.2-vision, etc.) if available.
"""

import os
import time
import logging
import tempfile
from typing import Optional
from memory import DATA_DIR

logger = logging.getLogger("aria.clipboard")

SCREENSHOT_DIR = DATA_DIR / "aria_screenshots"


def _pyperclip_available() -> bool:
    try:
        import pyperclip  # noqa: F401
        return True
    except ImportError:
        return False


def _screenshot_available() -> bool:
    try:
        import mss    # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


# ── Clipboard ─────────────────────────────────────────────────────────────────

def read_clipboard() -> str:
    """Return the current clipboard text content."""
    if not _pyperclip_available():
        return (
            "Clipboard access requires **pyperclip**.\n"
            "Install it with: `pip install pyperclip`"
        )
    try:
        import pyperclip
        text = pyperclip.paste()
        if not text or not text.strip():
            return "The clipboard is empty."
        preview = text[:1000]
        suffix = f"\n\n*...({len(text) - 1000} more characters)*" if len(text) > 1000 else ""
        return f"**Clipboard contents:**\n\n```\n{preview}\n```{suffix}"
    except Exception as e:
        return f"Couldn't read clipboard: {e}"


def write_clipboard(text: str) -> str:
    """Write text to the clipboard."""
    if not _pyperclip_available():
        return (
            "Clipboard access requires **pyperclip**.\n"
            "Install it with: `pip install pyperclip`"
        )
    try:
        import pyperclip
        pyperclip.copy(text)
        preview = text[:80] + ("..." if len(text) > 80 else "")
        return f"✅ Copied to clipboard: *{preview}*"
    except Exception as e:
        return f"Couldn't write to clipboard: {e}"


# ── Screenshot ────────────────────────────────────────────────────────────────

def take_screenshot(monitor: int = 0) -> dict:
    """
    Capture a screenshot and save it to a temp file.

    Returns:
        {"success": True,  "path": "/tmp/aria_screenshots/shot_123.png", "size": (1920,1080)}
        {"success": False, "error": "..."}
    """
    if not _screenshot_available():
        return {
            "success": False,
            "error": (
                "Screenshots require **mss** and **Pillow**.\n"
                "Install with: `pip install mss pillow`"
            )
        }

    try:
        import mss
        from PIL import Image

        SCREENSHOT_DIR.mkdir(exist_ok=True)
        filename = f"shot_{int(time.time())}.png"
        path = SCREENSHOT_DIR / filename

        with mss.mss() as sct:
            monitors = sct.monitors  # [0] = all screens combined, [1+] = individual
            idx = min(monitor, len(monitors) - 1)
            shot = sct.grab(monitors[idx])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            img.save(str(path), "PNG")

        logger.info(f"[Screenshot] Saved: {path}")
        return {
            "success": True,
            "path": str(path),
            "size": img.size,
        }

    except Exception as e:
        logger.error(f"[Screenshot] Failed: {e}")
        return {"success": False, "error": str(e)}


def describe_screenshot_with_llm(image_path: str, ollama_host: str, model: str) -> Optional[str]:
    """
    Ask a vision-capable Ollama model to describe the screenshot.
    Falls back gracefully if the model doesn't support vision.

    Requires a vision model like: llava, llama3.2-vision, moondream
    """
    import base64
    import json
    import urllib.request

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        payload = {
            "model": model,
            "prompt": (
                "Describe what you see on this screenshot concisely. "
                "Focus on the most important visible content: open apps, "
                "text on screen, and what the user appears to be doing."
            ),
            "images": [img_b64],
            "stream": False,
            "options": {"num_predict": 300},
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{ollama_host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip() or None

    except Exception as e:
        logger.warning(f"[Screenshot] Vision LLM failed: {e}")
        return None


# ── Tool entry points (called from aria_brain.py) ─────────────────────────────

def handle_clipboard_read() -> str:
    return read_clipboard()


def handle_clipboard_write(text: str) -> str:
    return write_clipboard(text)


def handle_screenshot(ollama_host: str = "http://localhost:11434",
                      vision_model: Optional[str] = None) -> str:
    result = take_screenshot()
    if not result["success"]:
        return result["error"]

    w, h = result["size"]
    path = result["path"]
    base = f"📸 Screenshot saved: `{path}` ({w}×{h}px)"

    if vision_model:
        description = describe_screenshot_with_llm(path, ollama_host, vision_model)
        if description:
            return f"{base}\n\n**What I see:** {description}"

    return (
        f"{base}\n\n"
        "*To enable AI description of screenshots, pull a vision model:*\n"
        "`ollama pull llava` and set `OLLAMA_VISION_MODEL=llava` in your .env"
    )