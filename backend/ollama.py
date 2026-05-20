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
import asyncio
import httpx
from typing import AsyncIterator, Iterator
from settings import (
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_GENERATE_TIMEOUT,
    OLLAMA_HOST as SETTINGS_OLLAMA_HOST,
    OLLAMA_MODEL as SETTINGS_OLLAMA_MODEL,
)

logger = logging.getLogger("aria.ollama")

# ── Configuration ─────────────────────────────────────────────────────────────

OLLAMA_HOST  = SETTINGS_OLLAMA_HOST
OLLAMA_MODEL = SETTINGS_OLLAMA_MODEL

# Timeouts (seconds)
_CONNECT_TIMEOUT  = OLLAMA_CONNECT_TIMEOUT
_GENERATE_TIMEOUT = OLLAMA_GENERATE_TIMEOUT

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

async def _post_async(path: str, payload: dict, timeout: float) -> dict:
    """POST JSON to Ollama and return the parsed response dict."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{OLLAMA_HOST}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()


async def _get_async(path: str, timeout: float = _CONNECT_TIMEOUT) -> dict:
    """GET JSON from Ollama."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{OLLAMA_HOST}{path}")
        resp.raise_for_status()
        return resp.json()


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Synchronous Ollama API called from a running event loop")


def _get_sync(path: str, timeout: float = _CONNECT_TIMEOUT) -> dict:
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(f"{OLLAMA_HOST}{path}")
        resp.raise_for_status()
        return resp.json()


def _post_sync(path: str, payload: dict, timeout: float) -> dict:
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{OLLAMA_HOST}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()


# ── Public API ────────────────────────────────────────────────────────────────

async def is_available_async() -> bool:
    try:
        await _get_async("/api/tags")
        return True
    except Exception:
        return False


def is_available() -> bool:
    """
    Return True if the Ollama server is reachable.
    Fails fast (3 s timeout) so startup isn't held up.
    """
    try:
        _get_sync("/api/tags")
        return True
    except Exception:
        return False


async def list_models_async() -> list[str]:
    try:
        data = await _get_async("/api/tags")
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def list_models() -> list[str]:
    """
    Return names of locally-pulled models, e.g. ['mistral:latest', 'llama3:latest'].
    Returns an empty list if Ollama is unreachable.
    """
    try:
        data = _get_sync("/api/tags")
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

    memory_entries = [e for e in history if e.get("role") in {"memory", "summary"}]
    for entry in memory_entries[-10:]:
        role = "Memory" if entry.get("role") == "memory" else "Conversation Summary"
        text = entry.get("text", "").strip()
        if text:
            lines.append(f"[{role}]\n{text}")

    turns = [e for e in history if e.get("role") not in {"memory", "summary"}]
    for entry in turns[-6:]:
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


async def generate_async(user_input: str, history: list[dict]) -> str:
    """
    Send user_input + conversation history to Ollama and return the
    model's response as a plain string.

    Raises:
        RuntimeError  if Ollama is unreachable or returns an error.
        TimeoutError  if the model takes longer than _GENERATE_TIMEOUT seconds.
    """
    prompt = _build_prompt(user_input, history)

    try:
        data = await _post_async(
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
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"Ollama timed out after {_GENERATE_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ollama unreachable: {exc}") from exc

    response = data.get("response", "").strip()
    if not response:
        raise RuntimeError("Ollama returned an empty response")

    return response


def generate(user_input: str, history: list[dict]) -> str:
    prompt = _build_prompt(user_input, history)
    try:
        data = _post_sync(
            "/api/generate",
            {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "num_predict": 512,
                },
            },
            timeout=_GENERATE_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"Ollama timed out after {_GENERATE_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ollama unreachable: {exc}") from exc
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
    prompt = _build_prompt(user_input, history)
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 512,
        },
    }

    try:
        with httpx.Client(timeout=_GENERATE_TIMEOUT) as client:
            with client.stream("POST", f"{OLLAMA_HOST}/api/generate", json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
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
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"Ollama stream timed out after {_GENERATE_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ollama unreachable: {exc}") from exc


async def generate_stream_async(user_input: str, history: list[dict]) -> AsyncIterator[str]:
    """
    Async streaming generator — yields text chunks from Ollama without
    blocking the event loop.

    Uses httpx streaming so FastAPI / uvicorn stay responsive while the
    model is generating, and cancellation can propagate from the WebSocket.

    Usage in an async context:
        async for chunk in generate_stream_async(text, history):
            await ws.send_text(json.dumps({"type": "stream_chunk", "text": chunk}))
    """
    prompt = _build_prompt(user_input, history)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
            "num_predict": 512,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=_GENERATE_TIMEOUT) as client:
            async with client.stream("POST", f"{OLLAMA_HOST}/api/generate", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
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
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"Ollama stream timed out after {_GENERATE_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Ollama unreachable: {exc}") from exc


def model_capabilities(model_name: str) -> set[str]:
    name = model_name.lower()
    caps = {"text"}
    if any(key in name for key in ("code", "coder", "deepseek-coder", "qwen2.5-coder")):
        caps.add("code")
    if any(key in name for key in ("llava", "bakllava", "moondream", "vision", "minicpm-v")):
        caps.add("vision")
    return caps


async def models_with_capabilities_async() -> list[dict]:
    models = await list_models_async()
    return [{"name": m, "capabilities": sorted(model_capabilities(m))} for m in models]
