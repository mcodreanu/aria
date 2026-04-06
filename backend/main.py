"""
ARIA Backend — FastAPI + WebSocket server
Run with: uvicorn main:app --reload --port 8000
"""

import json
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from memory import Memory
from aria_brain import process

app = FastAPI(title="ARIA", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")

# One memory instance per WebSocket connection
sessions: dict[str, Memory] = {}


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND / "index.html"))


@app.get("/health")
async def health():
    return JSONResponse({"status": "online", "name": "ARIA"})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = str(id(ws))
    memory = Memory()
    sessions[session_id] = memory

    # Greet on connect
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

            # Echo user message back (so frontend can display it)
            await ws.send_text(json.dumps({"type": "user", "text": user_text}))

            # Typing indicator
            await ws.send_text(json.dumps({"type": "typing"}))

            # Process and respond
            response = process(user_text, memory)

            await ws.send_text(json.dumps({"type": "aria", "text": response}))

    except WebSocketDisconnect:
        sessions.pop(session_id, None)