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
import json
import httpx
from pathlib import Path
from typing import Optional
from settings import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_VISION_MODEL,
    UPLOAD_DIR,
    UPLOAD_MAX_IMAGE_BYTES,
    UPLOAD_MAX_TEXT_CHARS,
    UPLOAD_MAX_TRANSFORM_CHARS,
)

logger = logging.getLogger("aria.upload")

UPLOAD_META_FILE = UPLOAD_DIR / ".uploads.json"

# Max file size we'll try to read fully into memory for LLM context (bytes)
MAX_TEXT_CHARS = UPLOAD_MAX_TEXT_CHARS
MAX_IMAGE_BYTES = UPLOAD_MAX_IMAGE_BYTES


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


def _load_index() -> dict:
    try:
        if UPLOAD_META_FILE.exists():
            data = json.loads(UPLOAD_META_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[Upload] index load failed: {e}")
    return {}


def _save_index(index: dict) -> None:
    try:
        tmp = UPLOAD_META_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        os.replace(tmp, UPLOAD_META_FILE)
    except Exception as e:
        logger.warning(f"[Upload] index save failed: {e}")


def _guess_mime(name: str, content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if content[:512].find(b"\0") == -1:
        guessed, _ = mimetypes.guess_type(name)
        return guessed or "text/plain"
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _safe_upload_path(filename: str) -> Path:
    path = (UPLOAD_DIR / Path(filename).name).resolve()
    root = UPLOAD_DIR.resolve()
    if path != root and root not in path.parents:
        raise PermissionError("Download path is outside upload storage.")
    return path


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text_file(path: Path) -> str:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + f"\n\n...[truncated at {MAX_TEXT_CHARS//1024}KB]"
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
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "\n\n...[truncated]"
                break
        return text.strip() or "[PDF contained no extractable text]"
    except ImportError:
        return "[PDF extraction requires pymupdf: pip install pymupdf]"
    except Exception as e:
        return f"[PDF read error: {e}]"


def _image_to_base64(path: Path) -> Optional[str]:
    try:
        data = path.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            return None
        return base64.b64encode(data).decode()
    except Exception:
        return None


# ── Ollama helpers ────────────────────────────────────────────────────────────

def _ask_ollama(prompt: str, context: str,
                image_b64: Optional[str] = None,
                vision_model: Optional[str] = None) -> str:
    host  = OLLAMA_HOST
    model = vision_model or OLLAMA_MODEL

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
        with httpx.Client(timeout=60) as client:
            resp = client.post(f"{host}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()
    except Exception as e:
        return f"[Ollama error: {e}]"


# ── Save uploaded file ────────────────────────────────────────────────────────

def save_upload(filename: str, content: bytes) -> dict:
    """
    Save an uploaded file to UPLOAD_DIR.
    Returns metadata dict: {id, path, name, mime, size}
    """
    safe_name = Path(filename).name or "upload"
    file_id   = uuid.uuid4().hex
    suffix    = Path(safe_name).suffix[:16]
    dest      = UPLOAD_DIR / f"{file_id}{suffix}"
    dest.write_bytes(content)

    mime = _guess_mime(safe_name, content)
    meta = {
        "id":   file_id,
        "path": str(dest),
        "name": safe_name,
        "mime": mime,
        "size": len(content),
    }
    index = _load_index()
    index[file_id] = meta
    _save_index(index)

    return {
        "id":   file_id,
        "name": safe_name,
        "mime": mime,
        "size": len(content),
    }


def get_upload_meta(file_id: str) -> Optional[dict]:
    if not file_id or not all(c in "0123456789abcdefABCDEF" for c in file_id):
        return None
    meta = _load_index().get(file_id)
    if not meta:
        return None
    path = _safe_upload_path(meta["path"])
    if not path.exists():
        return None
    return {**meta, "path": str(path)}


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
        vision_model = OLLAMA_VISION_MODEL or None
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
    if len(transformed) > UPLOAD_MAX_TRANSFORM_CHARS:
        transformed = transformed[:UPLOAD_MAX_TRANSFORM_CHARS] + "\n\n...[truncated]"

    # Save transformed version
    out_name = f"transformed_{uuid.uuid4().hex}{Path(name).suffix[:16]}"
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
    meta = get_upload_meta(file_id)
    return Path(meta["path"]) if meta else None


def cleanup_old_uploads(max_age_hours: int = 24):
    """Delete uploads older than max_age_hours."""
    import time
    cutoff = time.time() - max_age_hours * 3600
    index = _load_index()
    changed = False
    for f in UPLOAD_DIR.iterdir():
        if f.name.startswith("."):
            continue
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                for key, meta in list(index.items()):
                    if meta.get("path") == str(f):
                        index.pop(key, None)
                        changed = True
            except Exception:
                pass
    if changed:
        _save_index(index)
