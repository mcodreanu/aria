"""
ARIA Backend — FastAPI + WebSocket + Kokoro TTS
Run with: uvicorn main:app --reload --port 8000
"""

import json
import time
import logging
import asyncio
import httpx
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from memory import DATA_DIR, Memory
from aria_brain import process, process_stream, MUSIC_PLAY_PREFIX, MUSIC_STOP_PREFIX
import ollama as ollama_engine
import sysinfo.tts as tts_engine
import sysinfo.music as music_engine
import sysinfo.scheduler as scheduler_engine
from sysinfo.conversations import store as conv_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aria")

SESSION_TTL_SECONDS = 3600
MAX_SESSIONS        = 200

# session_id (str) → (Memory, float ts, int conv_session_id)
sessions: dict[str, tuple[Memory, float, int]] = {}

# Active WebSocket connections for push notifications: conv_session_id → ws
_ws_connections: dict[int, WebSocket] = {}


def _touch(session_id, memory, conv_sid):
    sessions[session_id] = (memory, time.time(), conv_sid)

def _prune_sessions():
    now = time.time()
    for sid in [s for s,(_, ts, _c) in sessions.items() if now-ts > SESSION_TTL_SECONDS]:
        sessions.pop(sid, None)
    if len(sessions) > MAX_SESSIONS:
        for sid in sorted(sessions, key=lambda s: sessions[s][1])[:len(sessions)-MAX_SESSIONS]:
            sessions.pop(sid, None)


# ── Scheduler push callback ───────────────────────────────────────────────────

async def _scheduler_push(conv_session_id, text: str):
    """Called by the scheduler when a reminder fires. Sends a WS message."""
    ws = _ws_connections.get(conv_session_id)
    if not ws:
        logger.info(f"[Scheduler] No active WS for session {conv_session_id}, reminder dropped.")
        return
    try:
        await ws.send_text(json.dumps({"type": "aria", "text": text}))
    except Exception as e:
        logger.warning(f"[Scheduler] Push failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # TTS
    if tts_engine.is_available():
        logger.info("Preloading Kokoro TTS engine...")
        await asyncio.get_event_loop().run_in_executor(None, tts_engine.preload)
    else:
        logger.warning("kokoro-onnx not found — install: pip install kokoro-onnx soundfile")

    # Ollama
    if ollama_engine.is_available():
        models = ollama_engine.list_models()
        active = ollama_engine.active_model()
        logger.info(f"Ollama online — active: {active} | pulled: {', '.join(models) or 'none'}")
        if active not in " ".join(models):
            logger.warning(f"Model '{active}' not pulled. Run: ollama pull {active}")
    else:
        logger.warning("Ollama not reachable — install: https://ollama.com/download")

    # Music
    if music_engine.is_available():
        logger.info("yt-dlp detected — music playback enabled.")
    else:
        logger.warning("yt-dlp not installed — music disabled. Run: pip install yt-dlp")

    # Scheduler — pass a shared Memory-like object for persistence
    _shared_memory = Memory()
    scheduler_engine.init(_scheduler_push, _shared_memory)

    logger.info(f"Conversation history DB: {conv_store._db_path}")

    yield

    music_engine.stop()
    logger.info("ARIA shutting down.")


app = FastAPI(title="ARIA", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


# ── Static routes ─────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(FRONTEND / "index.html"))

@app.get("/history")
async def history_page():
    return FileResponse(str(FRONTEND / "history.html"))

@app.get("/health")
async def health():
    ollama_up = ollama_engine.is_available()
    track = music_engine.current_track()
    stats = conv_store.stats()
    return JSONResponse({
        "status": "online", "name": "ARIA",
        "tts": "kokoro" if tts_engine.is_available() else "browser-fallback",
        "llm": ollama_engine.active_model() if ollama_up else "unavailable",
        "music": music_engine.is_available(),
        "now_playing": track["title"] if track else None,
        "scheduler": scheduler_engine.is_available(),
        "active_sessions": len(sessions),
        "total_conversations": stats.get("total_sessions", 0),
        "total_messages": stats.get("total_messages", 0),
    })


# ── TTS ───────────────────────────────────────────────────────────────────────

@app.get("/tts/status")
async def tts_status():
    return JSONResponse({"available": tts_engine.is_available()})

class TTSRequest(BaseModel):
    text: str
    voice: str = tts_engine.ARIA_VOICE
    speed: float = tts_engine.ARIA_SPEED

@app.post("/tts")
async def text_to_speech(req: TTSRequest):
    if not tts_engine.is_available():
        raise HTTPException(status_code=503, detail="TTS engine not available")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    try:
        wav_bytes = await asyncio.get_event_loop().run_in_executor(
            None, lambda: tts_engine.synthesize(req.text, req.voice, req.speed)
        )
        return Response(content=wav_bytes, media_type="audio/wav",
                        headers={"Cache-Control": "no-cache"})
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


# ── Ollama ────────────────────────────────────────────────────────────────────

@app.get("/ollama/status")
async def ollama_status():
    available = ollama_engine.is_available()
    return JSONResponse({
        "available": available,
        "model": ollama_engine.active_model() if available else None,
        "models": ollama_engine.list_models() if available else [],
    })

@app.get("/ollama/models")
async def ollama_models():
    return JSONResponse({"models": ollama_engine.list_models()})


# ── Music ─────────────────────────────────────────────────────────────────────

@app.get("/music/stream/{stream_id}")
async def music_stream_proxy(stream_id: str, request: Request):
    webpage_url = music_engine.get_webpage_url(stream_id)
    if not webpage_url:
        raise HTTPException(status_code=404, detail="Stream not found or expired")

    fresh_url = await asyncio.get_event_loop().run_in_executor(
        None, lambda: music_engine.fresh_stream_url(webpage_url)
    )
    if not fresh_url:
        raise HTTPException(status_code=502, detail="Could not re-extract audio URL")

    range_header = request.headers.get("Range")
    headers = {"User-Agent": "Mozilla/5.0"}
    if range_header:
        headers["Range"] = range_header

    async def _stream():
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            async with client.stream("GET", fresh_url, headers=headers) as resp:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as probe:
        head = await probe.head(fresh_url, headers={"User-Agent": "Mozilla/5.0"})

    content_type   = head.headers.get("content-type", "audio/webm")
    content_length = head.headers.get("content-length")
    accept_ranges  = head.headers.get("accept-ranges", "bytes")

    response_headers = {
        "Content-Type":  content_type,
        "Accept-Ranges": accept_ranges,
        "Cache-Control": "no-cache",
    }
    if content_length:
        response_headers["Content-Length"] = content_length

    status_code = 206 if range_header and head.status_code == 206 else 200
    return StreamingResponse(_stream(), status_code=status_code, headers=response_headers)

@app.get("/music/status")
async def music_status():
    track = music_engine.current_track()
    return JSONResponse({
        "available": music_engine.is_available(),
        "playing": track is not None,
        "track": track,
    })

@app.post("/music/stop")
async def music_stop_endpoint():
    music_engine.stop()
    return JSONResponse({"stopped": True})


# ── Conversation history API ──────────────────────────────────────────────────

@app.get("/history/sessions")
async def history_sessions(limit: int = 50):
    """List recent sessions with preview and message count."""
    sessions_list = await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.sessions(limit)
    )
    return JSONResponse({"sessions": sessions_list})

@app.get("/history/sessions/{session_id}")
async def history_session_messages(session_id: int):
    """Get all messages for a specific session."""
    messages = await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.session_messages(session_id)
    )
    return JSONResponse({"session_id": session_id, "messages": messages})

@app.delete("/history/sessions/{session_id}")
async def history_delete_session(session_id: int):
    """Delete a session and all its messages."""
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.delete_session(session_id)
    )
    return JSONResponse({"deleted": True, "session_id": session_id})

@app.get("/history/search")
async def history_search(q: str, limit: int = 20):
    """Full-text search across all conversation history."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.search(q, limit)
    )
    return JSONResponse({"query": q, "results": results, "count": len(results)})

@app.get("/history/stats")
async def history_stats():
    stats = await asyncio.get_event_loop().run_in_executor(None, conv_store.stats)
    return JSONResponse(stats)


# ── Music WebSocket helpers ───────────────────────────────────────────────────

async def _handle_music_play(query: str, ws: WebSocket, memory: Memory) -> None:
    await ws.send_text(json.dumps({"type": "stream_start"}))
    await ws.send_text(json.dumps({"type": "stream_chunk",
                                    "text": f"🔍 Searching for **{query}**..."}))
    await ws.send_text(json.dumps({"type": "stream_end"}))

    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: music_engine.search_and_prepare(query)
    )

    if result["success"]:
        mins, secs = divmod(result["duration"], 60)
        dur_str = f"{mins}:{secs:02d}" if mins else f"{secs}s"

        await ws.send_text(json.dumps({
            "type":      "music_play",
            "title":     result["title"],
            "url":       f"/music/stream/{result['stream_id']}",
            "duration":  result["duration"],
            "thumbnail": result.get("thumbnail", ""),
        }))

        await ws.send_text(json.dumps({"type": "stream_start"}))
        await ws.send_text(json.dumps({
            "type": "stream_chunk",
            "text": f"🎵 Now playing: **{result['title']}** ({dur_str})",
        }))
        await ws.send_text(json.dumps({"type": "stream_end", "no_tts": True}))
        memory.add("aria", f"Now playing: {result['title']}")
    else:
        await ws.send_text(json.dumps({"type": "stream_start"}))
        await ws.send_text(json.dumps({"type": "stream_chunk",
                                        "text": f"❌ {result['error']}"}))
        await ws.send_text(json.dumps({"type": "stream_end"}))


async def _handle_music_stop(ws: WebSocket, memory: Memory) -> None:
    music_engine.stop()
    await ws.send_text(json.dumps({"type": "music_stop"}))
    await ws.send_text(json.dumps({"type": "stream_start"}))
    await ws.send_text(json.dumps({"type": "stream_chunk", "text": "⏹ Music stopped."}))
    await ws.send_text(json.dumps({"type": "stream_end"}))
    memory.add("aria", "Music stopped.")


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _prune_sessions()

    ws_id = str(id(ws))
    memory = Memory()

    # Create a persistent conversation session in SQLite
    conv_sid = await asyncio.get_event_loop().run_in_executor(
        None, conv_store.new_session
    )

    # Store the conv_session_id in memory so handlers can reference it
    memory.remember("_session_id", str(conv_sid))

    _touch(ws_id, memory, conv_sid)
    _ws_connections[conv_sid] = ws

    welcome = "ARIA online. All systems operational. How can I assist you?"
    await ws.send_text(json.dumps({"type": "aria", "text": welcome}))

    # Persist the welcome message
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.add(conv_sid, "aria", welcome)
    )

    try:
        while True:
            raw  = await ws.receive_text()
            data = json.loads(raw)

            if data.get("type") == "music_ended":
                music_engine.stop()
                continue

            user_text = data.get("text", "").strip()
            if not user_text:
                continue

            _touch(ws_id, memory, conv_sid)

            # Persist user message
            await asyncio.get_event_loop().run_in_executor(
                None, lambda t=user_text: conv_store.add(conv_sid, "user", t)
            )

            await ws.send_text(json.dumps({"type": "user", "text": user_text}))
            await ws.send_text(json.dumps({"type": "typing"}))

            gen = process_stream(user_text, memory)
            first_chunk = None
            async for chunk in gen:
                first_chunk = chunk
                break

            if first_chunk is None:
                continue

            if first_chunk.startswith(MUSIC_PLAY_PREFIX):
                await _handle_music_play(first_chunk[len(MUSIC_PLAY_PREFIX):], ws, memory)
                continue

            if first_chunk.startswith(MUSIC_STOP_PREFIX):
                await _handle_music_stop(ws, memory)
                continue

            # Normal streaming response — collect for persistence
            await ws.send_text(json.dumps({"type": "stream_start"}))
            await ws.send_text(json.dumps({"type": "stream_chunk", "text": first_chunk}))

            full_response = first_chunk
            async for chunk in gen:
                if chunk:
                    await ws.send_text(json.dumps({"type": "stream_chunk", "text": chunk}))
                    full_response += chunk

            await ws.send_text(json.dumps({"type": "stream_end"}))

            # Persist ARIA's response
            await asyncio.get_event_loop().run_in_executor(
                None, lambda r=full_response: conv_store.add(conv_sid, "aria", r)
            )

    except WebSocketDisconnect:
        sessions.pop(ws_id, None)
        _ws_connections.pop(conv_sid, None)
        music_engine.stop()
        logger.info(f"[WS] session={ws_id[:8]} conv={conv_sid} disconnected")
    except Exception as exc:
        logger.error(f"[WS] session={ws_id[:8]} error: {exc}", exc_info=True)
        sessions.pop(ws_id, None)
        _ws_connections.pop(conv_sid, None)
        music_engine.stop()