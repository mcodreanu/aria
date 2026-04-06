"""
ARIA Backend — FastAPI + WebSocket + Kokoro TTS
Run with: uvicorn main:app --reload --port 8000
"""

import json
import time
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from memory import Memory
from aria_brain import process
import tts as tts_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aria")

# ── Session store ─────────────────────────────────────────────────────────────
#
# FIX: previously sessions grew forever — disconnected clients were removed
# on WebSocketDisconnect, but any session that died without a clean close
# (browser crash, network drop) would leak.
#
# We now store (Memory, last_seen_timestamp) and prune stale entries
# periodically.  The purge runs at most once per new connection (cheap) and
# removes sessions idle for longer than SESSION_TTL_SECONDS.
#
SESSION_TTL_SECONDS = 3600        # 1 hour idle → evict
MAX_SESSIONS        = 200         # hard cap; oldest evicted first

sessions: dict[str, tuple[Memory, float]] = {}


def _touch(session_id: str, memory: Memory) -> None:
    sessions[session_id] = (memory, time.time())


def _prune_sessions() -> None:
    """Remove stale and overflow sessions."""
    now = time.time()
    # Evict expired sessions
    expired = [sid for sid, (_, ts) in sessions.items()
               if now - ts > SESSION_TTL_SECONDS]
    for sid in expired:
        sessions.pop(sid, None)

    # Enforce hard cap: drop oldest if still over limit
    if len(sessions) > MAX_SESSIONS:
        sorted_sids = sorted(sessions, key=lambda s: sessions[s][1])
        for sid in sorted_sids[:len(sessions) - MAX_SESSIONS]:
            sessions.pop(sid, None)


# ── Lifespan: preload Kokoro on startup ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if tts_engine.is_available():
        logger.info("Preloading Kokoro TTS engine...")
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, tts_engine.preload)
    else:
        logger.warning(
            "kokoro-onnx not found — TTS will be unavailable. "
            "Install with: pip install kokoro-onnx soundfile"
        )
    yield
    logger.info("ARIA shutting down.")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="ARIA", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(FRONTEND / "index.html"))


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "online",
        "name": "ARIA",
        "tts": "kokoro" if tts_engine.is_available() else "browser-fallback",
        "active_sessions": len(sessions),
    })


@app.get("/tts/status")
async def tts_status():
    """Frontend polls this to know whether to use Kokoro or browser TTS."""
    return JSONResponse({"available": tts_engine.is_available()})


class TTSRequest(BaseModel):
    text: str
    voice: str = tts_engine.ARIA_VOICE
    speed: float = tts_engine.ARIA_SPEED


@app.post("/tts")
async def text_to_speech(req: TTSRequest):
    """
    Convert text to speech using Kokoro.
    Returns audio/wav bytes.
    Falls back gracefully: if Kokoro unavailable, returns 503 so the
    frontend can fall back to browser speechSynthesis.
    """
    if not tts_engine.is_available():
        raise HTTPException(status_code=503, detail="TTS engine not available")

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        wav_bytes = await loop.run_in_executor(
            None,
            lambda: tts_engine.synthesize(req.text, req.voice, req.speed)
        )
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # Prune dead sessions on every new connection (amortised, cheap)
    _prune_sessions()

    session_id = str(id(ws))
    memory = Memory()
    _touch(session_id, memory)

    await ws.send_text(json.dumps({
        "type": "aria",
        "text": "ARIA online. All systems operational. How can I assist you?",
    }))

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            user_text = data.get("text", "").strip()

            if not user_text:
                continue

            # Refresh last-seen so idle sessions aren't wrongly evicted
            _touch(session_id, memory)

            await ws.send_text(json.dumps({"type": "user", "text": user_text}))
            await ws.send_text(json.dumps({"type": "typing"}))

            response = process(user_text, memory)

            await ws.send_text(json.dumps({"type": "aria", "text": response}))

    except WebSocketDisconnect:
        sessions.pop(session_id, None)