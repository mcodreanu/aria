"""
ollama.py — Ollama local LLM integration for ARIA.

Communicates with a locally-running Ollama server (https://ollama.com).
No API keys required — Ollama runs entirely on your machine.

Configuration (via environment variables or .env):
    OLLAMA_HOST   Base URL of your Ollama server. Default: http://localhost:11434
    OLLAMA_MODEL  Model to use.                   Default: mistral

Quick-start:
    1. Install Ollama: https://ollama.com/download
    2. Pull a model:   ollama pull mistral
    3. Start ARIA — it will detect Ollama automatically.

Supported models (anything Ollama supports works; these are recommended):
    mistral       — fast, 7B, great general-purpose responses  ← default
    llama3        — Meta's Llama 3, strong reasoning
    llama3.2      — smaller/faster Llama 3 variant
    phi3          — Microsoft, very fast on CPU
    gemma2        — Google, good instruction following
    qwen2         — Alibaba, strong multilingual
    deepseek-r1   — strong at reasoning / math
"""

import os
import json
import logging
import urllib.request
import urllib.error
from typing import AsyncIterator, Iterator

logger = logging.getLogger("aria.ollama")

# ── Configuration ─────────────────────────────────────────────────────────────

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")

# Timeouts (seconds)
_CONNECT_TIMEOUT  = 3    # availability check — fail fast
_GENERATE_TIMEOUT = 60   # generation — models can be slow on CPU

# System prompt — shapes ARIA's personality when the LLM is used
_SYSTEM_PROMPT = """You are ARIA (Adaptive Reasoning & Intelligent Assistant), a local AI assistant running on the user's own machine.

Personality:
- Concise and direct. Prefer short answers unless detail is explicitly asked for.
- Technical and precise. Use correct terminology.
- Slightly futuristic tone — think Jarvis, not a customer-service chatbot.
- Never claim to be ChatGPT, Claude, or any other cloud AI.

Formatting:
- Use **bold** for key terms and important values.
- Use `code` for commands, filenames, and expressions.
- Use bullet lists only when listing 3+ distinct items.
- Never add unnecessary preamble like "Of course!" or "Great question!".
- Keep responses under ~200 words unless the user explicitly asks for more detail.

Constraints:
- You run entirely locally. Do not mention cloud services or APIs.
- If you don't know something, say so clearly instead of guessing.
- The user may have already received a web-search result; you are the reasoning layer on top of it.
"""


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _post(path: str, payload: dict, timeout: int) -> dict:
    """POST JSON to Ollama and return the parsed response dict."""
    url  = f"{OLLAMA_HOST}{path}"
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(path: str, timeout: int = _CONNECT_TIMEOUT) -> dict:
    """GET JSON from Ollama."""
    url = f"{OLLAMA_HOST}{path}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── Public API ────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """
    Return True if the Ollama server is reachable.
    Fails fast (3 s timeout) so startup isn't held up.
    """
    try:
        _get("/api/tags")
        return True
    except Exception:
        return False


def list_models() -> list[str]:
    """
    Return names of locally-pulled models, e.g. ['mistral:latest', 'llama3:latest'].
    Returns an empty list if Ollama is unreachable.
    """
    try:
        data = _get("/api/tags")
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def active_model() -> str:
    """Return the model name ARIA will use."""
    return OLLAMA_MODEL


def _build_prompt(user_input: str, history: list[dict]) -> str:
    """
    Build a plain-text prompt that includes recent conversation turns so the
    LLM has context.  We use Mistral/Llama's simple Human/Assistant format
    which all major Ollama models understand without special tokens.

    history entries look like: {"role": "user"|"aria", "text": "..."}
    We include the last 6 turns (3 exchanges) to stay within context limits
    while keeping the conversation coherent.
    """
    lines = [f"[System]\n{_SYSTEM_PROMPT.strip()}\n"]

    for entry in history[-6:]:
        role  = entry.get("role", "")
        text  = entry.get("text", "").strip()
        if not text:
            continue
        if role == "user":
            lines.append(f"[User]\n{text}")
        elif role == "aria":
            lines.append(f"[ARIA]\n{text}")

    lines.append(f"[User]\n{user_input}")
    lines.append("[ARIA]")
    return "\n\n".join(lines)


def generate(user_input: str, history: list[dict]) -> str:
    """
    Send user_input + conversation history to Ollama and return the
    model's response as a plain string.

    Raises:
        RuntimeError  if Ollama is unreachable or returns an error.
        TimeoutError  if the model takes longer than _GENERATE_TIMEOUT seconds.
    """
    prompt = _build_prompt(user_input, history)

    try:
        data = _post(
            "/api/generate",
            {
                "model":  OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,           # wait for the full response
                "options": {
                    "temperature": 0.7,    # balanced creativity vs accuracy
                    "top_p": 0.9,
                    "num_predict": 512,    # max output tokens (~400 words)
                },
            },
            timeout=_GENERATE_TIMEOUT,
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama unreachable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise TimeoutError(f"Ollama timed out after {_GENERATE_TIMEOUT}s") from exc

    response = data.get("response", "").strip()
    if not response:
        raise RuntimeError("Ollama returned an empty response")

    return response


def generate_stream(user_input: str, history: list[dict]) -> Iterator[str]:
    """
    Synchronous streaming variant — yields text chunks as they arrive.
    Used by the async wrapper below via run_in_executor.
    Each chunk is a small string (a few tokens).

    Raises RuntimeError / TimeoutError on connection failure.
    """
    import socket

    prompt = _build_prompt(user_input, history)
    url    = f"{OLLAMA_HOST}/api/generate"
    body   = json.dumps({
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 512,
        },
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_GENERATE_TIMEOUT) as resp:
            for raw_line in resp:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                token = chunk.get("response", "")
                if token:
                    yield token

                if chunk.get("done", False):
                    break
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama unreachable: {exc.reason}") from exc
    except socket.timeout as exc:
        raise TimeoutError(f"Ollama stream timed out after {_GENERATE_TIMEOUT}s") from exc


async def generate_stream_async(user_input: str, history: list[dict]) -> AsyncIterator[str]:
    """
    Async streaming generator — yields text chunks from Ollama without
    blocking the event loop.

    Wraps generate_stream() (synchronous, blocking I/O) using asyncio's
    thread-pool executor so FastAPI / uvicorn stay responsive while the
    model is generating.

    Usage in an async context:
        async for chunk in generate_stream_async(text, history):
            await ws.send_text(json.dumps({"type": "stream_chunk", "text": chunk}))
    """
    import asyncio
    import queue
    import threading

    loop = asyncio.get_event_loop()
    q: queue.Queue[str | None] = queue.Queue()
    exc_holder: list[Exception] = []

    def _producer():
        """Run the blocking generator in a thread; push tokens onto the queue."""
        try:
            for token in generate_stream(user_input, history):
                q.put(token)
        except Exception as e:
            exc_holder.append(e)
        finally:
            q.put(None)  # sentinel

    thread = threading.Thread(target=_producer, daemon=True)
    thread.start()

    while True:
        # Poll the queue without blocking the event loop
        try:
            token = await loop.run_in_executor(None, q.get)
        except Exception:
            break

        if token is None:
            break  # sentinel received — generation complete

        yield token

    thread.join(timeout=1)

    if exc_holder:
        raise exc_holder[0]