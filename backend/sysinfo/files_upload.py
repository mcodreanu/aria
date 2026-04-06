"""
files_upload.py — File upload handling for ARIA.

Supports:
  - Text files (.txt, .md, .py, .js, .csv, .json, .html, .css, etc.)
  - PDF text extraction
  - Images — returned as base64 for inline display + optional Ollama vision
  - Any file — transform/edit via Ollama and offer download

Install optional deps:
    pip install pymupdf pillow   (PDF + image support)
"""

import os
import uuid
import base64
import logging
import mimetypes
from pathlib import Path
from typing import Optional
from memory import DATA_DIR

logger = logging.getLogger("aria.upload")

UPLOAD_DIR = DATA_DIR / "aria_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Max file size we'll try to read fully into memory for LLM context (bytes)
MAX_TEXT_BYTES = 128 * 1024   # 128 KB
MAX_IMAGE_B64  = 4 * 1024 * 1024  # 4 MB base64


# ── MIME helpers ──────────────────────────────────────────────────────────────

def _is_text(mime: str) -> bool:
    return (
        mime.startswith("text/") or
        mime in {
            "application/json", "application/xml",
            "application/javascript", "application/x-python",
            "application/x-sh",
        }
    )

def _is_pdf(mime: str) -> bool:
    return mime == "application/pdf"

def _is_image(mime: str) -> bool:
    return mime.startswith("image/")


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text_file(path: Path) -> str:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if len(text) > MAX_TEXT_BYTES:
            text = text[:MAX_TEXT_BYTES] + f"\n\n...[truncated at {MAX_TEXT_BYTES//1024}KB]"
        return text
    except Exception as e:
        return f"[Could not read file: {e}]"


def _extract_pdf(path: Path) -> str:
    try:
        import fitz  # pymupdf
        doc  = fitz.open(str(path))
        text = ""
        for page in doc:
            text += page.get_text()
            if len(text) > MAX_TEXT_BYTES:
                text = text[:MAX_TEXT_BYTES] + "\n\n...[truncated]"
                break
        return text.strip() or "[PDF contained no extractable text]"
    except ImportError:
        return "[PDF extraction requires pymupdf: pip install pymupdf]"
    except Exception as e:
        return f"[PDF read error: {e}]"


def _image_to_base64(path: Path) -> Optional[str]:
    try:
        data = path.read_bytes()
        if len(data) > MAX_IMAGE_B64:
            return None
        return base64.b64encode(data).decode()
    except Exception:
        return None


# ── Ollama helpers ────────────────────────────────────────────────────────────

def _ask_ollama(prompt: str, context: str,
                image_b64: Optional[str] = None,
                vision_model: Optional[str] = None) -> str:
    import json
    import urllib.request
    from os import getenv

    host  = getenv("OLLAMA_HOST",  "http://localhost:11434").rstrip("/")
    model = vision_model or getenv("OLLAMA_MODEL", "mistral")

    payload: dict = {
        "model":  model,
        "stream": False,
        "options": {"num_predict": 1024, "temperature": 0.3},
    }

    if image_b64:
        payload["prompt"] = prompt
        payload["images"] = [image_b64]
    else:
        payload["prompt"] = (
            f"You are ARIA, a helpful local AI assistant.\n\n"
            f"FILE CONTENT:\n{context}\n\n"
            f"USER REQUEST:\n{prompt}\n\n"
            f"Respond concisely and helpfully."
        )

    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "").strip()
    except Exception as e:
        return f"[Ollama error: {e}]"


# ── Save uploaded file ────────────────────────────────────────────────────────

def save_upload(filename: str, content: bytes) -> dict:
    """
    Save an uploaded file to UPLOAD_DIR.
    Returns metadata dict: {id, path, name, mime, size}
    """
    safe_name = Path(filename).name  # strip any path traversal
    file_id   = str(uuid.uuid4())[:8]
    dest      = UPLOAD_DIR / f"{file_id}_{safe_name}"
    dest.write_bytes(content)

    mime, _ = mimetypes.guess_type(safe_name)
    mime    = mime or "application/octet-stream"

    return {
        "id":   file_id,
        "path": str(dest),
        "name": safe_name,
        "mime": mime,
        "size": len(content),
    }


# ── Main entry points ─────────────────────────────────────────────────────────

def handle_file_question(file_meta: dict, question: str) -> dict:
    """
    Answer a question about an uploaded file.
    Returns {"type": "text"|"image", "content": str, "download_id": optional}
    """
    path = Path(file_meta["path"])
    mime = file_meta["mime"]
    name = file_meta["name"]

    if not path.exists():
        return {"type": "text", "content": f"File `{name}` no longer exists."}

    # ── Image ──
    if _is_image(mime):
        b64 = _image_to_base64(path)
        vision_model = os.getenv("OLLAMA_VISION_MODEL", "").strip() or None
        if b64 and vision_model:
            desc = _ask_ollama(
                question or "Describe this image in detail.",
                context="",
                image_b64=b64,
                vision_model=vision_model,
            )
            return {
                "type":    "image",
                "content": desc,
                "b64":     b64,
                "mime":    mime,
                "name":    name,
            }
        return {
            "type":    "image",
            "content": f"Image uploaded: **{name}**",
            "b64":     b64,
            "mime":    mime,
            "name":    name,
        }

    # ── PDF ──
    if _is_pdf(mime):
        text = _extract_pdf(path)
        if not question:
            question = "Summarize this document."
        answer = _ask_ollama(question, text)
        return {"type": "text", "content": answer}

    # ── Text / code / CSV / JSON ──
    if _is_text(mime):
        text = _extract_text_file(path)
        if not question:
            question = "Summarize this file."
        answer = _ask_ollama(question, text)
        return {"type": "text", "content": answer}

    return {
        "type":    "text",
        "content": (
            f"Uploaded **{name}** ({file_meta['size']//1024} KB, `{mime}`).\n"
            "I can read text, PDF, and image files. "
            "Ask me a question about it or say 'summarize'."
        ),
    }


def handle_file_transform(file_meta: dict, instruction: str) -> dict:
    """
    Transform a text/code file according to an instruction and return a download.
    Returns {"type": "download", "content": str, "filename": str} or error.
    """
    path = Path(file_meta["path"])
    mime = file_meta["mime"]
    name = file_meta["name"]

    if not path.exists():
        return {"type": "text", "content": f"File `{name}` no longer exists."}

    if _is_image(mime) or _is_pdf(mime):
        return {"type": "text", "content": "Transform works on text/code files only."}

    original = _extract_text_file(path)
    prompt   = (
        f"Apply the following transformation to the file content.\n"
        f"Return ONLY the transformed file content, no explanation.\n\n"
        f"INSTRUCTION: {instruction}"
    )
    transformed = _ask_ollama(prompt, original)

    # Save transformed version
    out_name = f"transformed_{name}"
    out_path = UPLOAD_DIR / out_name
    out_path.write_text(transformed, encoding="utf-8")

    return {
        "type":     "download",
        "content":  transformed,
        "filename": out_name,
        "path":     str(out_path),
    }


def get_upload_file(file_id: str) -> Optional[Path]:
    """Find a previously uploaded file by its ID prefix."""
    for f in UPLOAD_DIR.iterdir():
        if f.name.startswith(file_id):
            return f
    return None


def cleanup_old_uploads(max_age_hours: int = 24):
    """Delete uploads older than max_age_hours."""
    import time
    cutoff = time.time() - max_age_hours * 3600
    for f in UPLOAD_DIR.iterdir():
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
            except Exception:
                pass