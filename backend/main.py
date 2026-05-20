"""
ARIA Backend — FastAPI + WebSocket + Kokoro TTS
Run with: uvicorn main:app --reload --port 8000
"""

import json
import time
import logging
import asyncio
import httpx
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from memory import DATA_DIR, Memory
from aria_brain import process, process_stream, MUSIC_PLAY_PREFIX, MUSIC_STOP_PREFIX, ACTION_PENDING_PREFIX
import ollama as ollama_engine
import sysinfo.tts as tts_engine
import sysinfo.stt as stt_engine
import sysinfo.music as music_engine
import sysinfo.scheduler as scheduler_engine
import sysinfo.prefs as prefs_engine
import sysinfo.actions as actions_engine
import sysinfo.typed_memory as typed_memory_engine
import sysinfo.tasks as tasks_engine
import sysinfo.plugins as plugins_engine
import sysinfo.workspace_index as workspace_engine
from sysinfo.conversations import store as conv_store
from sysinfo.files_upload import get_upload_meta, save_upload, handle_file_question, handle_file_transform, cleanup_old_uploads, UPLOAD_DIR
from health import ttl_cached
from settings import CORS_ORIGINS, MAX_SESSIONS, SESSION_TTL_SECONDS, TTS_CACHE_MAX_ITEMS, UPLOAD_MAX_BYTES
import ws_protocol as wsp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aria")

# session_id (str) → (Memory, float ts, int conv_session_id)
sessions: dict[str, tuple[Memory, float, int]] = {}
client_sessions: dict[str, tuple[Memory, float, int]] = {}

# Active WebSocket connections for push notifications: conv_session_id → ws
_ws_connections: dict[int, WebSocket] = {}
_tts_cache: dict[tuple[str, str, float], bytes] = {}


def _touch(session_id, memory, conv_sid):
    sessions[session_id] = (memory, time.time(), conv_sid)

def _prune_sessions():
    now = time.time()
    for sid in [s for s,(_, ts, _c) in sessions.items() if now-ts > SESSION_TTL_SECONDS]:
        sessions.pop(sid, None)
    for sid in [s for s,(_, ts, _c) in client_sessions.items() if now-ts > SESSION_TTL_SECONDS]:
        client_sessions.pop(sid, None)
    if len(sessions) > MAX_SESSIONS:
        for sid in sorted(sessions, key=lambda s: sessions[s][1])[:len(sessions)-MAX_SESSIONS]:
            sessions.pop(sid, None)
    if len(client_sessions) > MAX_SESSIONS:
        for sid in sorted(client_sessions, key=lambda s: client_sessions[s][1])[:len(client_sessions)-MAX_SESSIONS]:
            client_sessions.pop(sid, None)


# ── Scheduler push callback ───────────────────────────────────────────────────

async def _scheduler_push(conv_session_id, text: str):
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
    # TTS — apply saved preferences
    saved_prefs = prefs_engine.get_all()
    if saved_prefs.get("tts_voice"):
        tts_engine.ARIA_VOICE = saved_prefs["tts_voice"]
    if saved_prefs.get("tts_speed"):
        tts_engine.ARIA_SPEED = float(saved_prefs["tts_speed"])

    if tts_engine.is_available():
        logger.info("Preloading Kokoro TTS engine...")
        await asyncio.get_event_loop().run_in_executor(None, tts_engine.preload)
    else:
        logger.warning("kokoro-onnx not found — install: pip install kokoro-onnx soundfile")

    if stt_engine.is_available():
        logger.info(f"Local STT enabled — engine: {stt_engine.engine_name()}")
    else:
        logger.warning(f"Local STT unavailable — {stt_engine.install_hint()}")

    # Ollama — apply saved model preference
    saved_model = saved_prefs.get("ollama_model")
    if saved_model:
        ollama_engine.OLLAMA_MODEL = saved_model

    if ollama_engine.is_available():
        models = ollama_engine.list_models()
        active = ollama_engine.active_model()
        logger.info(f"Ollama online — active: {active} | pulled: {', '.join(models) or 'none'}")
    else:
        logger.warning("Ollama not reachable — install: https://ollama.com/download")

    if music_engine.is_available():
        logger.info("yt-dlp detected — music playback enabled.")
    else:
        logger.warning("yt-dlp not installed — music disabled. Run: pip install yt-dlp")

    _shared_memory = Memory()
    scheduler_engine.init(_scheduler_push, _shared_memory)

    # Clean up old uploads on startup
    await asyncio.get_event_loop().run_in_executor(None, cleanup_old_uploads)

    logger.info(f"Conversation history DB: {conv_store._db_path}")
    logger.info(f"Preferences file: {prefs_engine.PREFS_FILE}")

    yield

    music_engine.stop()
    logger.info("ARIA shutting down.")


app = FastAPI(title="ARIA", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


# ── Static routes ─────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(FRONTEND / "index.html"))

@app.get("/history")
async def history_page():
    return FileResponse(str(FRONTEND / "history.html"))

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(str(FRONTEND / "dashboard.html"))

@app.get("/health")
async def health():
    ollama_up = ttl_cached("ollama_up", ollama_engine.is_available)
    track     = music_engine.current_track()
    stats     = ttl_cached("conv_stats", conv_store.stats)
    return JSONResponse({
        "status":               "online",
        "name":                 "ARIA",
        "tts":                  "kokoro" if ttl_cached("tts_up", tts_engine.is_available) else "browser-fallback",
        "stt":                  stt_engine.engine_name() or "unavailable",
        "llm":                  ollama_engine.active_model() if ollama_up else "unavailable",
        "music":                ttl_cached("music_up", music_engine.is_available),
        "now_playing":          track["title"] if track else None,
        "scheduler":            scheduler_engine.is_available(),
        "active_sessions":      len(sessions),
        "total_conversations":  stats.get("total_sessions", 0),
        "total_messages":       stats.get("total_messages", 0),
    })


# ── Dashboard sysinfo endpoint ────────────────────────────────────────────────

@app.get("/dashboard/sysinfo")
async def dashboard_sysinfo():
    try:
        import psutil, platform, datetime
        cpu     = psutil.cpu_percent(interval=0.2)
        ram     = psutil.virtual_memory()
        disk    = psutil.disk_usage("/")
        boot_ts = psutil.boot_time()
        uptime  = str(datetime.timedelta(seconds=int(time.time() - boot_ts)))
        return JSONResponse({
            "cpu_pct":      round(cpu, 1),
            "ram_pct":      round(ram.percent, 1),
            "ram_used_gb":  round(ram.used / 1e9, 1),
            "ram_total_gb": round(ram.total / 1e9, 1),
            "disk_pct":     round(disk.percent, 1),
            "disk_used_gb": round(disk.used / 1e9, 1),
            "disk_total_gb":round(disk.total / 1e9, 1),
            "os":           f"{platform.system()} {platform.release()}",
            "python":       platform.python_version(),
            "uptime":       uptime,
        })
    except ImportError:
        raise HTTPException(status_code=503, detail="psutil not installed")


# ── Preferences API ───────────────────────────────────────────────────────────

@app.get("/prefs")
async def get_prefs():
    return JSONResponse(prefs_engine.get_all())

@app.patch("/prefs")
async def update_prefs(request: Request):
    body = await request.json()
    try:
        updated = prefs_engine.update(body)
        # Apply model change immediately if provided
        if "ollama_model" in body:
            ollama_engine.OLLAMA_MODEL = body["ollama_model"]
        # Apply TTS changes immediately if provided
        if "tts_voice" in body:
            tts_engine.ARIA_VOICE = body["tts_voice"]
        if "tts_speed" in body:
            tts_engine.ARIA_SPEED = float(body["tts_speed"])
        return JSONResponse(updated)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/prefs/reset")
async def reset_prefs():
    return JSONResponse(prefs_engine.reset())


class MemoryRecordRequest(BaseModel):
    type: str = "note"
    key: str = "note"
    value: str
    source: str = "api"
    confidence: float = 1.0

class TaskRequest(BaseModel):
    title: str
    notes: str = ""
    status: str = "todo"
    priority: str = "normal"
    due_at: float | None = None

class TaskPatchRequest(BaseModel):
    title: str | None = None
    notes: str | None = None
    status: str | None = None
    priority: str | None = None
    due_at: float | None = None


# ── Feature expansion APIs ───────────────────────────────────────────────────

@app.get("/actions")
async def actions_list(status: str | None = None):
    return JSONResponse({
        "actions": actions_engine.list_actions(status),
        "trust": actions_engine.list_trust(),
    })

@app.post("/actions/{action_id}/approve")
async def actions_approve(action_id: str):
    action = actions_engine.approve_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return JSONResponse(action)

@app.post("/actions/{action_id}/reject")
async def actions_reject(action_id: str):
    action = actions_engine.reject_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return JSONResponse(action)

@app.delete("/actions/trust")
async def actions_revoke_trust(tool: str, scope: str = "*"):
    if not actions_engine.revoke_trust(tool, scope):
        raise HTTPException(status_code=404, detail="Trusted approval not found")
    return JSONResponse({"revoked": True, "tool": tool, "scope": scope})

@app.get("/memory")
async def memory_list(type: str | None = None):
    return JSONResponse({"records": typed_memory_engine.list_records(type)})

@app.post("/memory")
async def memory_create(req: MemoryRecordRequest):
    return JSONResponse(typed_memory_engine.add_record(
        req.type, req.key, req.value, req.source, req.confidence
    ))

@app.delete("/memory/{record_id}")
async def memory_delete(record_id: str):
    if not typed_memory_engine.delete_record(record_id):
        raise HTTPException(status_code=404, detail="Memory record not found")
    return JSONResponse({"deleted": True, "id": record_id})

@app.get("/tasks")
async def tasks_list(status: str | None = None):
    return JSONResponse({"tasks": tasks_engine.list_tasks(status)})

@app.post("/tasks")
async def tasks_create(req: TaskRequest):
    return JSONResponse(tasks_engine.add_task(
        req.title, req.notes, req.status, req.priority, req.due_at
    ))

@app.patch("/tasks/{task_id}")
async def tasks_patch(task_id: str, req: TaskPatchRequest):
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    updates = {k: v for k, v in data.items() if v is not None}
    task = tasks_engine.update_task(task_id, updates)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse(task)

@app.delete("/tasks/{task_id}")
async def tasks_delete(task_id: str):
    if not tasks_engine.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse({"deleted": True, "id": task_id})

@app.get("/plugins")
async def plugins_list():
    return JSONResponse({"plugins": plugins_engine.list_plugins()})

@app.post("/plugins/{plugin_id}/enable")
async def plugin_enable(plugin_id: str):
    plugin = plugins_engine.set_enabled(plugin_id, True)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return JSONResponse(plugin)

@app.post("/plugins/{plugin_id}/disable")
async def plugin_disable(plugin_id: str):
    plugin = plugins_engine.set_enabled(plugin_id, False)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return JSONResponse(plugin)

@app.post("/workspace/index")
async def workspace_reindex():
    data = await asyncio.get_event_loop().run_in_executor(None, workspace_engine.build_index)
    return JSONResponse({"indexed_at": data["indexed_at"], "count": len(data["files"])})

@app.get("/workspace/search")
async def workspace_search(q: str, limit: int = 20):
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    return JSONResponse({"query": q, "results": workspace_engine.search(q, limit)})

@app.get("/workspace/files")
async def workspace_files():
    return JSONResponse({"files": workspace_engine.files()})

@app.get("/diagnostics")
async def diagnostics():
    ollama_up = ttl_cached("ollama_up", ollama_engine.is_available)
    return JSONResponse({
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "stt": {"available": stt_engine.is_available(), "engine": stt_engine.engine_name()},
        "tts": {"available": tts_engine.is_available(), "cache_items": len(_tts_cache)},
        "ollama": {
            "available": ollama_up,
            "model": ollama_engine.active_model() if ollama_up else None,
            "models": ttl_cached("ollama_models", ollama_engine.list_models) if ollama_up else [],
        },
        "scheduler": {"available": scheduler_engine.is_available()},
        "sessions": {"active": len(sessions), "resumable": len(client_sessions)},
        "actions": {"pending": len(actions_engine.list_actions("pending"))},
        "tasks": {"open": len([t for t in tasks_engine.list_tasks() if t.get("status") != "done"])},
        "workspace": {"indexed_files": len(workspace_engine.files())},
    })


# ── File upload API ───────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Receive a file upload, save it, return metadata."""
    content = await file.read()
    if len(content) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {UPLOAD_MAX_BYTES//1024//1024}MB)")
    meta = await asyncio.get_event_loop().run_in_executor(
        None, lambda: save_upload(file.filename or "upload", content)
    )
    return JSONResponse(meta)

class FileQuestionRequest(BaseModel):
    file_id: str
    filename: str
    mime: str
    question: str = ""

@app.post("/upload/ask")
async def ask_about_file(req: FileQuestionRequest):
    """Ask a question about a previously uploaded file."""
    meta = get_upload_meta(req.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    meta.update({"name": req.filename, "mime": req.mime})
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: handle_file_question(meta, req.question)
    )
    return JSONResponse(result)

class FileTransformRequest(BaseModel):
    file_id:     str
    filename:    str
    mime:        str
    instruction: str

@app.post("/upload/transform")
async def transform_file(req: FileTransformRequest):
    """Transform a file according to an instruction and return download."""
    meta = get_upload_meta(req.file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    meta.update({"name": req.filename, "mime": req.mime})
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: handle_file_transform(meta, req.instruction)
    )
    return JSONResponse(result)

@app.get("/upload/download/{filename}")
async def download_transformed(filename: str):
    path = (UPLOAD_DIR / Path(filename).name).resolve()
    root = UPLOAD_DIR.resolve()
    if path != root and root not in path.parents:
        raise HTTPException(status_code=403, detail="Invalid download path")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=filename)


# ── STT ───────────────────────────────────────────────────────────────────────

@app.get("/stt/status")
async def stt_status():
    return JSONResponse({
        "available": stt_engine.is_available(),
        "engine": stt_engine.engine_name(),
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "hint": None if stt_engine.is_available() else stt_engine.install_hint(),
    })

@app.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Audio too large (max {UPLOAD_MAX_BYTES//1024//1024}MB)")
    suffix = Path(file.filename or "speech.webm").suffix or ".webm"
    try:
        text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: stt_engine.transcribe_bytes(content, suffix)
        )
        return JSONResponse({"text": text})
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=f"Speech recognition failed: {str(e)}")


# ── TTS ───────────────────────────────────────────────────────────────────────

@app.get("/tts/status")
async def tts_status():
    return JSONResponse({"available": tts_engine.is_available()})

class TTSRequest(BaseModel):
    text:  str
    voice: str   = tts_engine.ARIA_VOICE
    speed: float = tts_engine.ARIA_SPEED

@app.post("/tts")
async def text_to_speech(req: TTSRequest):
    if not tts_engine.is_available():
        raise HTTPException(status_code=503, detail="TTS engine not available")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    # Use current runtime prefs as fallback
    voice = req.voice or tts_engine.ARIA_VOICE
    speed = req.speed or tts_engine.ARIA_SPEED
    cache_key = (req.text, voice, round(float(speed), 2))
    if cache_key in _tts_cache:
        return Response(content=_tts_cache[cache_key], media_type="audio/wav",
                        headers={"Cache-Control": "private, max-age=300"})
    try:
        wav_bytes = await asyncio.get_event_loop().run_in_executor(
            None, lambda: tts_engine.synthesize(req.text, voice, speed)
        )
        _tts_cache[cache_key] = wav_bytes
        if len(_tts_cache) > TTS_CACHE_MAX_ITEMS:
            _tts_cache.pop(next(iter(_tts_cache)))
        return Response(content=wav_bytes, media_type="audio/wav",
                        headers={"Cache-Control": "no-cache"})
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


# ── Ollama ────────────────────────────────────────────────────────────────────

@app.get("/ollama/status")
async def ollama_status():
    available = ttl_cached("ollama_up", ollama_engine.is_available)
    models = ttl_cached("ollama_models", ollama_engine.list_models)
    return JSONResponse({
        "available": available,
        "model":     ollama_engine.active_model() if available else None,
        "models":    models if available else [],
        "capabilities": [
            {"name": m, "capabilities": sorted(ollama_engine.model_capabilities(m))}
            for m in models
        ] if available else [],
    })

@app.get("/ollama/models")
async def ollama_models():
    models = ttl_cached("ollama_models", ollama_engine.list_models)
    return JSONResponse({
        "models": models,
        "capabilities": [
            {"name": m, "capabilities": sorted(ollama_engine.model_capabilities(m))}
            for m in models
        ],
    })


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

    content_type    = head.headers.get("content-type", "audio/webm")
    content_length  = head.headers.get("content-length")
    accept_ranges   = head.headers.get("accept-ranges", "bytes")

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
        "playing":   track is not None,
        "track":     track,
    })

@app.post("/music/stop")
async def music_stop_endpoint():
    music_engine.stop()
    return JSONResponse({"stopped": True})


# ── Conversation history API ──────────────────────────────────────────────────

@app.get("/history/sessions")
async def history_sessions(limit: int = 50):
    sessions_list = await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.sessions(limit)
    )
    return JSONResponse({"sessions": sessions_list})

@app.get("/history/sessions/{session_id}")
async def history_session_messages(session_id: int):
    messages = await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.session_messages(session_id)
    )
    return JSONResponse({"session_id": session_id, "messages": messages})

@app.delete("/history/sessions/{session_id}")
async def history_delete_session(session_id: int):
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: conv_store.delete_session(session_id)
    )
    return JSONResponse({"deleted": True, "session_id": session_id})

@app.get("/history/search")
async def history_search(q: str, limit: int = 20):
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
    client_id = ws.query_params.get("client_session_id") or ws_id
    resumed = client_id in client_sessions
    if resumed:
        memory, _ts, conv_sid = client_sessions[client_id]
    else:
        memory = Memory()
        conv_sid = await asyncio.get_event_loop().run_in_executor(
            None, conv_store.new_session
        )
        memory.remember("_session_id", str(conv_sid))

    _touch(ws_id, memory, conv_sid)
    client_sessions[client_id] = (memory, time.time(), conv_sid)
    _ws_connections[conv_sid] = ws
    active_task: asyncio.Task | None = None

    welcome = "ARIA online. All systems operational. How can I assist you?"
    await ws.send_text(json.dumps({"type": wsp.ARIA, "text": welcome, "session_id": conv_sid}))
    if not resumed:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: conv_store.add(conv_sid, "aria", welcome)
        )

    async def _send_error(message: str):
        try:
            await ws.send_text(json.dumps({"type": wsp.ERROR, "message": message}))
        except Exception:
            pass

    async def _handle_file_ask(file_meta: dict, question: str):
        if not file_meta:
            return
        server_meta = get_upload_meta(str(file_meta.get("id", "")))
        if not server_meta:
            await _send_error("Uploaded file was not found or has expired.")
            return
        server_meta.update({
            "name": file_meta.get("name", server_meta["name"]),
            "mime": file_meta.get("mime", server_meta["mime"]),
        })
        await ws.send_text(json.dumps({"type": wsp.TYPING}))
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: handle_file_question(server_meta, question)
        )
        await ws.send_text(json.dumps({"type": wsp.STREAM_START}))
        if result["type"] == "image":
            await ws.send_text(json.dumps({
                "type":    wsp.FILE_IMAGE,
                "b64":     result.get("b64", ""),
                "mime":    result.get("mime", "image/png"),
                "name":    result.get("name", "image"),
                "caption": result.get("content", ""),
            }))
        else:
            await ws.send_text(json.dumps({
                "type": wsp.STREAM_CHUNK,
                "text": result["content"],
            }))
        await ws.send_text(json.dumps({"type": wsp.STREAM_END}))

    async def _process_user_text(user_text: str):
        _touch(ws_id, memory, conv_sid)
        client_sessions[client_id] = (memory, time.time(), conv_sid)
        await asyncio.get_event_loop().run_in_executor(
            None, lambda t=user_text: conv_store.add(conv_sid, "user", t)
        )

        await ws.send_text(json.dumps({"type": wsp.USER, "text": user_text}))
        await ws.send_text(json.dumps({"type": wsp.TYPING}))

        gen = process_stream(user_text, memory)
        first_chunk = None
        async for chunk in gen:
            first_chunk = chunk
            break

        if first_chunk is None:
            return

        if first_chunk.startswith(MUSIC_PLAY_PREFIX):
            await _handle_music_play(first_chunk[len(MUSIC_PLAY_PREFIX):], ws, memory)
            return

        if first_chunk.startswith(MUSIC_STOP_PREFIX):
            await _handle_music_stop(ws, memory)
            return

        if first_chunk.startswith(ACTION_PENDING_PREFIX):
            action_id = first_chunk[len(ACTION_PENDING_PREFIX):]
            action = actions_engine.get_action(action_id)
            if action:
                await ws.send_text(json.dumps({"type": "action_pending", "action": action}))
                text = (
                    f"Approval needed for **{action['summary']}**. "
                    f"Open the action card or dashboard to approve or reject it. *(id: `{action_id}`)*"
                )
            else:
                text = "I tried to create a pending action, but it was not found."
            await ws.send_text(json.dumps({"type": wsp.STREAM_START}))
            await ws.send_text(json.dumps({"type": wsp.STREAM_CHUNK, "text": text}))
            await ws.send_text(json.dumps({"type": wsp.STREAM_END}))
            await asyncio.get_event_loop().run_in_executor(
                None, lambda r=text: conv_store.add(conv_sid, "aria", r)
            )
            return

        await ws.send_text(json.dumps({"type": wsp.STREAM_START}))
        await ws.send_text(json.dumps({"type": wsp.STREAM_CHUNK, "text": first_chunk}))

        full_response = first_chunk
        async for chunk in gen:
            if chunk:
                await ws.send_text(json.dumps({"type": wsp.STREAM_CHUNK, "text": chunk}))
                full_response += chunk

        await ws.send_text(json.dumps({"type": wsp.STREAM_END}))
        await asyncio.get_event_loop().run_in_executor(
            None, lambda r=full_response: conv_store.add(conv_sid, "aria", r)
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: conv_store.maybe_refresh_summary(conv_sid)
        )

    try:
        while True:
            raw  = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error("Malformed WebSocket message.")
                continue

            if data.get("type") == "music_ended":
                music_engine.stop()
                continue

            if data.get("type") == "stop":
                if active_task and not active_task.done():
                    active_task.cancel()
                    try:
                        await active_task
                    except asyncio.CancelledError:
                        pass
                    await ws.send_text(json.dumps({"type": wsp.STREAM_CANCELLED}))
                music_engine.stop()
                continue

            # ── File upload message ──
            if data.get("type") == "file_ask":
                active_task = asyncio.create_task(
                    _handle_file_ask(data.get("file"), data.get("question", "").strip())
                )
                continue

            user_text = data.get("text", "").strip()
            if not user_text:
                continue

            if active_task and not active_task.done():
                active_task.cancel()
                try:
                    await active_task
                except asyncio.CancelledError:
                    pass
                await ws.send_text(json.dumps({"type": wsp.STREAM_CANCELLED}))
            active_task = asyncio.create_task(_process_user_text(user_text))

    except WebSocketDisconnect:
        if active_task and not active_task.done():
            active_task.cancel()
        sessions.pop(ws_id, None)
        _ws_connections.pop(conv_sid, None)
        music_engine.stop()
        logger.info(f"[WS] session={ws_id[:8]} conv={conv_sid} disconnected")
    except Exception as exc:
        logger.error(f"[WS] session={ws_id[:8]} error: {exc}", exc_info=True)
        await _send_error("ARIA hit an internal WebSocket error.")
        if active_task and not active_task.done():
            active_task.cancel()
        sessions.pop(ws_id, None)
        _ws_connections.pop(conv_sid, None)
        music_engine.stop()
