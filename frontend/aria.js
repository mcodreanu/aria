// ─────────────────────────────────────────────────────────────────────────────
// ARIA Frontend — File upload · Copy buttons · Persistent prefs · Ordered TTS
// ─────────────────────────────────────────────────────────────────────────────

const chat = document.getElementById("chat");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const micBtn = document.getElementById("mic-btn");
const muteBtn = document.getElementById("mute-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const voiceStat = document.getElementById("voice-status");

let ws = null;
let typingEl = null;

// ─────────────────────────────────────────────────────────────────────────────
// Persistent preferences — load once at boot, apply immediately
// ─────────────────────────────────────────────────────────────────────────────

let _prefs = {
  muted: false,
  tts_voice: "af_heart",
  tts_speed: 1.0,
  theme: "dark",
  ollama_model: "mistral",
};

async function loadPrefs() {
  try {
    const r = await fetch("/prefs");
    if (r.ok) {
      _prefs = await r.json();
      applyPrefs();
    }
  } catch (_) {}
}

function applyPrefs() {
  isMuted = !!_prefs.muted;
  muteBtn.innerHTML = isMuted ? ICON_SPEAKER_OFF : ICON_SPEAKER_ON;
  muteBtn.classList.toggle("muted", isMuted);
  document.documentElement.setAttribute("data-theme", _prefs.theme || "dark");
}

async function savePref(key, value) {
  _prefs[key] = value;
  try {
    await fetch("/prefs", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
  } catch (_) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// TTS — ordered fetch chain → ordered playback queue
// ─────────────────────────────────────────────────────────────────────────────

let isMuted = false;
let isSpeaking = false;
let _ttsReady = false;
let audioQueue = [];
let currentAudio = null;
let _fetchChain = Promise.resolve();

const MIN_CHUNK = 8;
const EAGER_CHARS = 80;
const _BREAK_RE = /([.!?]['")\]]*(?:\s+|$)|[,;:]\s+)/g;

async function initTTS() {
  try {
    const r = await fetch("/tts/status");
    const data = await r.json();
    if (data.available) {
      setVoiceStatus("🎙 Kokoro voice ready", 3000);
      fetch("/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "." }),
      }).catch(() => {});
    } else {
      setVoiceStatus("⚠ Kokoro not available — voice disabled", 5000);
    }
  } catch {
    setVoiceStatus("⚠ Could not reach TTS — voice disabled", 5000);
  }
}

async function _doFetch(text) {
  const clean = text.trim();
  if (!clean || isMuted) return null;
  try {
    const res = await fetch("/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice: _prefs.tts_voice || "af_heart",
        speed: _prefs.tts_speed || 1.0,
      }),
    });
    if (!res.ok) return null;
    const blob = await res.blob();
    return { url: URL.createObjectURL(blob) };
  } catch {
    return null;
  }
}

function _playNext() {
  if (isMuted || audioQueue.length === 0) {
    isSpeaking = false;
    micBtn.classList.remove("speaking");
    return;
  }
  const item = audioQueue.shift();
  if (!item) {
    _playNext();
    return;
  }
  const audio = new Audio(item.url);
  currentAudio = audio;
  isSpeaking = true;
  micBtn.classList.add("speaking");
  const done = () => {
    URL.revokeObjectURL(item.url);
    currentAudio = null;
    _playNext();
  };
  audio.onended = done;
  audio.onerror = done;
  audio.play().catch(done);
}

function speakChunk(text) {
  if (isMuted || musicPlaying || !text.trim()) return;
  _fetchChain = _fetchChain.then(async () => {
    if (isMuted || musicPlaying) return;
    const item = await _doFetch(text);
    if (item) {
      audioQueue.push(item);
      if (!isSpeaking) _playNext();
    }
  });
}

function stopSpeaking() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  audioQueue = [];
  _fetchChain = Promise.resolve();
  isSpeaking = false;
  micBtn.classList.remove("speaking");
}

// ── Sentence buffer ──────────────────────────────────────────────────────────

let _sentenceBuffer = "";

function _flushSentences(text, force) {
  _sentenceBuffer += text;
  const buf = _sentenceBuffer;
  const chunks = [];
  let last = 0;

  _BREAK_RE.lastIndex = 0;
  let m;
  while ((m = _BREAK_RE.exec(buf)) !== null) {
    const end = m.index + m[0].length;
    const chunk = buf.slice(last, end).trim();
    if (chunk.length >= MIN_CHUNK) {
      chunks.push(chunk);
      last = end;
    }
  }

  const tail = buf.slice(last);
  if (tail.length >= EAGER_CHARS) {
    const cut = tail.lastIndexOf(" ", EAGER_CHARS);
    const splitAt = cut > MIN_CHUNK ? cut : EAGER_CHARS;
    const eager = tail.slice(0, splitAt).trim();
    if (eager.length >= MIN_CHUNK) {
      chunks.push(eager);
      last += splitAt;
    }
  }

  _sentenceBuffer = buf.slice(last);
  for (const s of chunks) speakChunk(stripMarkdown(s));
  if (force && _sentenceBuffer.trim().length >= MIN_CHUNK) {
    speakChunk(stripMarkdown(_sentenceBuffer.trim()));
    _sentenceBuffer = "";
  }
}

function resetSentenceBuffer() {
  _sentenceBuffer = "";
}

// ─────────────────────────────────────────────────────────────────────────────
// Mute toggle
// ─────────────────────────────────────────────────────────────────────────────

const ICON_SPEAKER_ON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;
const ICON_SPEAKER_OFF = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>`;

muteBtn.innerHTML = ICON_SPEAKER_ON;
muteBtn.addEventListener("click", () => {
  isMuted = !isMuted;
  muteBtn.classList.toggle("muted", isMuted);
  muteBtn.innerHTML = isMuted ? ICON_SPEAKER_OFF : ICON_SPEAKER_ON;
  muteBtn.title = isMuted ? "Unmute voice" : "Mute voice";
  savePref("muted", isMuted);
  if (isMuted) {
    stopSpeaking();
    setVoiceStatus("🔇 Voice muted", 2000);
  } else {
    setVoiceStatus("🔊 Voice on", 2000);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// File Upload
// ─────────────────────────────────────────────────────────────────────────────

let _pendingFile = null; // {id, name, mime, size} — set after successful upload

function injectUploadButton() {
  const inputRow = document.querySelector(".input-row");
  if (!inputRow || document.getElementById("upload-btn")) return;

  const btn = document.createElement("button");
  btn.id = "upload-btn";
  btn.title = "Upload file";
  btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66
             l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
  </svg>`;
  btn.style.cssText = `
    flex-shrink:0; background:var(--panel); border:1px solid var(--border);
    border-radius:2px; color:var(--text-dim); cursor:pointer; padding:10px;
    display:flex; align-items:center; justify-content:center;
    transition:border-color .2s,color .2s;
  `;
  btn.onmouseenter = () => {
    btn.style.borderColor = "var(--accent2)";
    btn.style.color = "var(--accent)";
  };
  btn.onmouseleave = () => {
    btn.style.borderColor = "var(--border)";
    btn.style.color = "var(--text-dim)";
  };
  btn.addEventListener("click", () => fileInputEl.click());

  inputRow.insertBefore(btn, inputRow.firstChild);
}

// Hidden file input
const fileInputEl = document.createElement("input");
fileInputEl.type = "file";
fileInputEl.accept =
  "image/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv,.html,.css,.xml,.yaml,.yml,.sh,.c,.cpp,.java,.go,.rs,.rb,.php";
fileInputEl.style.display = "none";
document.body.appendChild(fileInputEl);

fileInputEl.addEventListener("change", () => {
  const file = fileInputEl.files[0];
  if (file) handleFileSelected(file);
  fileInputEl.value = "";
});

// Drag & drop on the whole chat area
chat.addEventListener("dragover", (e) => {
  e.preventDefault();
  chat.style.borderColor = "var(--accent)";
});
chat.addEventListener("dragleave", () => {
  chat.style.borderColor = "";
});
chat.addEventListener("drop", (e) => {
  e.preventDefault();
  chat.style.borderColor = "";
  const file = e.dataTransfer.files[0];
  if (file) handleFileSelected(file);
});

async function handleFileSelected(file) {
  // Show upload indicator
  const uploadMsg = appendUploadIndicator(file.name);

  try {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/upload", { method: "POST", body: formData });
    if (!res.ok) throw new Error(await res.text());
    const meta = await res.json();

    _pendingFile = meta;
    updateUploadIndicator(uploadMsg, meta);

    // Pre-fill input with a prompt if it's blank
    if (!input.value.trim()) {
      input.value = "Summarize this file";
      input.focus();
    }
  } catch (err) {
    uploadMsg.innerHTML = `<span style="color:var(--danger)">❌ Upload failed: ${escHtml(String(err))}</span>`;
    _pendingFile = null;
  }
}

function appendUploadIndicator(filename) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.style.alignItems = "center";
  el.innerHTML = `
    <div class="msg-avatar">ME</div>
    <div class="msg-body" style="display:flex;align-items:center;gap:8px;
         font-size:.82rem;color:var(--text-dim)">
      <span style="animation:spin .8s linear infinite;display:inline-block">⏳</span>
      Uploading <strong>${escHtml(filename)}</strong>…
    </div>`;
  chat.appendChild(el);
  scrollBottom();
  return el.querySelector(".msg-body");
}

function updateUploadIndicator(bodyEl, meta) {
  const sizeKb = Math.round(meta.size / 1024);
  bodyEl.innerHTML = `
    📎 <strong>${escHtml(meta.name)}</strong>
    <span style="color:var(--text-dim);font-size:.75rem">${sizeKb} KB · ${meta.mime}</span>
    <button onclick="clearPendingFile()" style="background:transparent;border:none;
      color:var(--text-dim);cursor:pointer;font-size:.8rem;padding:0 4px" title="Remove">✕</button>`;
}

function clearPendingFile() {
  _pendingFile = null;
  // Remove indicator from chat
  const msgs = chat.querySelectorAll(".msg.user .msg-body");
  for (const m of msgs) {
    if (m.querySelector("button[onclick='clearPendingFile()']")) {
      m.closest(".msg").remove();
      break;
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Copy button on code blocks
// ─────────────────────────────────────────────────────────────────────────────

function addCopyButtons(container) {
  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return; // already has one
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "Copy";
    btn.title = "Copy code";
    btn.addEventListener("click", async () => {
      const code = pre.innerText;
      try {
        await navigator.clipboard.writeText(code);
        btn.textContent = "✓";
        btn.style.color = "var(--success)";
        setTimeout(() => {
          btn.textContent = "Copy";
          btn.style.color = "";
        }, 1800);
      } catch {
        btn.textContent = "Error";
        setTimeout(() => {
          btn.textContent = "Copy";
        }, 1800);
      }
    });
    pre.style.position = "relative";
    pre.appendChild(btn);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Music Player
// ─────────────────────────────────────────────────────────────────────────────

let musicPlaying = false;
let musicAudio = null;
let musicPlayerEl = null;
let _volBeforeMute = 1.0;
let _isMusicMuted = false;

function createMusicPlayer(title, url, duration, thumbnail) {
  destroyMusicPlayer(false);
  musicPlaying = true;

  musicPlayerEl = document.createElement("div");
  musicPlayerEl.id = "music-player";
  musicPlayerEl.innerHTML = `
    <div class="mp-top">
      ${
        thumbnail
          ? `<img class="mp-thumb" src="${thumbnail}" alt="" onerror="this.style.display='none'">`
          : `<div class="mp-thumb-placeholder">♪</div>`
      }
      <div class="mp-title-wrap">
        <div class="mp-title" title="${escHtml(title)}">${escHtml(title)}</div>
        <div class="mp-status playing" id="mp-status">▶ PLAYING</div>
      </div>
      <button id="mp-stop-btn" title="Stop music">
        <svg viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
      </button>
    </div>
    <div class="mp-seek-row">
      <span class="mp-time mp-elapsed" id="mp-elapsed">0:00</span>
      <div class="mp-seek-wrap">
        <div class="mp-bar-bg">
          <div class="mp-bar-buffer" id="mp-bar-buffer"></div>
          <div class="mp-bar-fill"   id="mp-bar-fill"></div>
        </div>
        <input type="range" class="mp-seek-input" id="mp-seek-input"
               min="0" max="${duration || 1000}" step="1" value="0">
      </div>
      <span class="mp-time mp-total">${fmtTime(duration)}</span>
    </div>
    <div class="mp-controls">
      <div class="mp-vol-wrap">
        <button class="mp-vol-icon" id="mp-mute-btn" title="Mute / Unmute">${_svgVolHigh()}</button>
        <input type="range" class="mp-vol-slider" id="mp-vol-slider" min="0" max="100" step="1" value="100">
        <span class="mp-vol-pct" id="mp-vol-pct">100%</span>
      </div>
      <div class="mp-spacer"></div>
      <span class="mp-speed-label">SPEED</span>
      <select class="mp-speed-select" id="mp-speed-select">
        <option value="0.5">0.5×</option><option value="0.75">0.75×</option>
        <option value="1" selected>1×</option><option value="1.25">1.25×</option>
        <option value="1.5">1.5×</option><option value="2">2×</option>
      </select>
    </div>`;

  document
    .querySelector("footer")
    .parentElement.insertBefore(
      musicPlayerEl,
      document.querySelector("footer"),
    );

  musicAudio = new Audio(url);
  musicAudio.crossOrigin = "anonymous";
  musicAudio.volume = 1.0;

  const seekInput = document.getElementById("mp-seek-input");
  const barFill = document.getElementById("mp-bar-fill");
  const barBuffer = document.getElementById("mp-bar-buffer");
  const elapsedEl = document.getElementById("mp-elapsed");
  let isSeeking = false;

  musicAudio.addEventListener("timeupdate", () => {
    if (isSeeking) return;
    const cur = musicAudio.currentTime,
      total = duration || musicAudio.duration || 1;
    barFill.style.width = `${(cur / total) * 100}%`;
    seekInput.value = Math.floor(cur);
    elapsedEl.textContent = fmtTime(Math.floor(cur));
  });
  musicAudio.addEventListener("progress", () => {
    if (!musicAudio.buffered.length) return;
    const total = duration || musicAudio.duration || 1;
    barBuffer.style.width = `${(musicAudio.buffered.end(musicAudio.buffered.length - 1) / total) * 100}%`;
  });
  seekInput.addEventListener("mousedown", () => {
    isSeeking = true;
  });
  seekInput.addEventListener(
    "touchstart",
    () => {
      isSeeking = true;
    },
    { passive: true },
  );
  seekInput.addEventListener("input", () => {
    elapsedEl.textContent = fmtTime(Number(seekInput.value));
    barFill.style.width = `${(seekInput.value / (duration || musicAudio.duration || 1)) * 100}%`;
  });
  seekInput.addEventListener("change", () => {
    musicAudio.currentTime = Number(seekInput.value);
    isSeeking = false;
  });
  musicAudio.addEventListener("loadedmetadata", () => {
    seekInput.max = Math.floor(musicAudio.duration || duration);
  });

  const volSlider = document.getElementById("mp-vol-slider");
  const volPct = document.getElementById("mp-vol-pct");
  const mpMuteBtn = document.getElementById("mp-mute-btn");

  volSlider.addEventListener("input", () => {
    const v = Number(volSlider.value) / 100;
    musicAudio.volume = v;
    _volBeforeMute = v;
    _isMusicMuted = v === 0;
    volPct.textContent = `${volSlider.value}%`;
    _updateVolIcon(v);
  });
  mpMuteBtn.addEventListener("click", () => {
    if (_isMusicMuted) {
      const r = _volBeforeMute > 0 ? _volBeforeMute : 0.8;
      musicAudio.volume = r;
      volSlider.value = Math.round(r * 100);
      volPct.textContent = `${Math.round(r * 100)}%`;
      _isMusicMuted = false;
      _updateVolIcon(r);
    } else {
      _volBeforeMute = musicAudio.volume;
      musicAudio.volume = 0;
      volSlider.value = 0;
      volPct.textContent = "0%";
      _isMusicMuted = true;
      _updateVolIcon(0);
    }
  });
  document.getElementById("mp-speed-select").addEventListener("change", (e) => {
    musicAudio.playbackRate = parseFloat(e.target.value);
  });
  musicAudio.addEventListener("ended", () => {
    if (ws?.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ type: "music_ended" }));
    fetch("/music/stop", { method: "POST" }).catch(() => {});
    destroyMusicPlayer(false);
  });
  musicAudio.addEventListener("error", () => {
    if (!musicAudio) return;
    destroyMusicPlayer(false);
    appendMessage("aria", "❌ Audio playback error — try again.");
  });
  musicAudio.addEventListener("waiting", () =>
    _setMpStatus("⏳ BUFFERING", false),
  );
  musicAudio.addEventListener("playing", () => _setMpStatus("▶ PLAYING", true));
  musicAudio.addEventListener("pause", () => _setMpStatus("⏸ PAUSED", false));
  document
    .getElementById("mp-stop-btn")
    .addEventListener("click", () => send("stop music"));

  musicAudio.play().catch((err) => {
    console.warn("[Music] Autoplay blocked:", err);
    appendMessage(
      "aria",
      "⚠ Browser blocked autoplay. Click anywhere then try again.",
    );
    destroyMusicPlayer(false);
  });
  window.addEventListener("beforeunload", _onPageUnload);
}

function _setMpStatus(text, playing) {
  const el = document.getElementById("mp-status");
  if (el) {
    el.textContent = text;
    el.className = "mp-status" + (playing ? " playing" : "");
  }
}
function _updateVolIcon(v) {
  const btn = document.getElementById("mp-mute-btn");
  if (btn)
    btn.innerHTML =
      v === 0 ? _svgVolOff() : v < 0.5 ? _svgVolLow() : _svgVolHigh();
}
function _svgVolHigh() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;
}
function _svgVolLow() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;
}
function _svgVolOff() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>`;
}

function destroyMusicPlayer(_) {
  musicPlaying = false;
  _isMusicMuted = false;
  if (musicAudio) {
    const d = musicAudio;
    musicAudio = null;
    d.pause();
    d.src = "";
    try {
      d.load();
    } catch (_) {}
  }
  if (musicPlayerEl) {
    musicPlayerEl.remove();
    musicPlayerEl = null;
  }
  window.removeEventListener("beforeunload", _onPageUnload);
}
function _onPageUnload() {
  navigator.sendBeacon("/music/stop");
}
function fmtTime(secs) {
  if (!secs || isNaN(secs)) return "0:00";
  return `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, "0")}`;
}
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket
// ─────────────────────────────────────────────────────────────────────────────

let currentStreamEl = null;
let streamBuffer = "";

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => setStatus(true);
  ws.onclose = () => {
    setStatus(false);
    setTimeout(connect, 3000);
  };
  ws.onerror = () => setStatus(false);

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);

    if (data.type === "typing") {
      showTyping();
    } else if (data.type === "aria") {
      hideTyping();
      appendMessage("aria", data.text);
      if (_ttsReady) speakChunk(stripMarkdown(data.text));
    } else if (data.type === "stream_start") {
      hideTyping();
      streamBuffer = "";
      resetSentenceBuffer();
      currentStreamEl = appendStreamMessage();
    } else if (data.type === "stream_chunk") {
      streamBuffer += data.text;
      if (currentStreamEl) {
        currentStreamEl.innerHTML = formatText(streamBuffer);
        addCopyButtons(currentStreamEl);
        scrollBottom();
      }
      if (!data.no_tts && !musicPlaying) _flushSentences(data.text, false);
    } else if (data.type === "stream_end") {
      if (currentStreamEl) {
        addCopyButtons(currentStreamEl);
        const timeEl = document.createElement("div");
        timeEl.className = "msg-time";
        timeEl.textContent = new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });
        currentStreamEl.parentElement.appendChild(timeEl);
        if (!data.no_tts && !musicPlaying) _flushSentences("", true);
        currentStreamEl = null;
        streamBuffer = "";
      }
    } else if (data.type === "file_image") {
      // Inline image from upload
      hideTyping();
      appendImageMessage(data.name, data.b64, data.mime, data.caption);
    } else if (data.type === "music_play") {
      stopSpeaking();
      createMusicPlayer(data.title, data.url, data.duration, data.thumbnail);
    } else if (data.type === "music_stop") {
      destroyMusicPlayer(false);
    }
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Send — with file upload awareness
// ─────────────────────────────────────────────────────────────────────────────

function send(text) {
  text = (text || input.value).trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  _ttsReady = true;
  stopSpeaking();

  if (_pendingFile) {
    // Send file question over WS
    appendMessage("user", `📎 **${_pendingFile.name}** — ${text}`);
    ws.send(
      JSON.stringify({
        type: "file_ask",
        file: _pendingFile,
        question: text,
      }),
    );
    _pendingFile = null;
    // Remove upload indicator bubble
    const msgs = chat.querySelectorAll(".msg.user .msg-body");
    for (const m of msgs) {
      if (m.querySelector("button[onclick='clearPendingFile()']")) {
        m.closest(".msg").remove();
        break;
      }
    }
  } else {
    appendMessage("user", text);
    ws.send(JSON.stringify({ text }));
  }

  input.value = "";
  input.focus();
}

sendBtn.addEventListener("click", () => send());
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
document.querySelectorAll(".qcmd").forEach((btn) => {
  btn.addEventListener("click", () => send(btn.dataset.cmd));
});

// ─────────────────────────────────────────────────────────────────────────────
// Speech Recognition (STT)
// ─────────────────────────────────────────────────────────────────────────────

const SpeechRecognition =
  window.SpeechRecognition || window.webkitSpeechRecognition;
const hasSTT = !!SpeechRecognition;
let wakeRecognizer = null,
  activeRecognizer = null,
  isListeningActive = false,
  wakeEnabled = hasSTT;
const WAKE_WORD = "hey aria";
const WAKE_ALTS = ["hey area", "hay aria", "hey arya", "hi aria", "aria"];

if (!hasSTT) {
  micBtn.title = "Speech recognition not supported — use Chrome or Edge";
  micBtn.style.opacity = "0.4";
  micBtn.style.cursor = "not-allowed";
  setVoiceStatus("⚠ Voice input needs Chrome or Edge", 4000);
}

function startWakeListener() {
  if (!hasSTT || isListeningActive) return;
  wakeRecognizer = new SpeechRecognition();
  wakeRecognizer.continuous = true;
  wakeRecognizer.interimResults = true;
  wakeRecognizer.lang = "en-US";
  wakeRecognizer.maxAlternatives = 3;
  wakeRecognizer.onstart = () => micBtn.classList.add("wake-active");
  wakeRecognizer.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++)
      for (let a = 0; a < event.results[i].length; a++) {
        const alt = event.results[i][a].transcript.toLowerCase().trim();
        if (alt.includes(WAKE_WORD) || WAKE_ALTS.some((w) => alt.includes(w))) {
          stopWakeListener();
          activateVoiceCommand();
          return;
        }
      }
  };
  wakeRecognizer.onerror = () => {};
  wakeRecognizer.onend = () => {
    micBtn.classList.remove("wake-active");
    if (!isListeningActive && wakeEnabled) setTimeout(startWakeListener, 300);
  };
  try {
    wakeRecognizer.start();
  } catch (_) {}
}

function stopWakeListener() {
  if (wakeRecognizer) {
    try {
      wakeRecognizer.stop();
    } catch (_) {}
    wakeRecognizer = null;
  }
  micBtn.classList.remove("wake-active");
}

function activateVoiceCommand() {
  if (!hasSTT || isListeningActive) return;
  isListeningActive = true;
  _ttsReady = true;
  stopSpeaking();
  playBeep(880, 80);
  micBtn.classList.add("listening");
  setVoiceStatus("🎤 Listening...");
  input.placeholder = "Listening...";

  activeRecognizer = new SpeechRecognition();
  activeRecognizer.continuous = false;
  activeRecognizer.interimResults = true;
  activeRecognizer.lang = "en-US";
  activeRecognizer.maxAlternatives = 1;

  let finalTranscript = "";
  activeRecognizer.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const t = event.results[i][0].transcript;
      event.results[i].isFinal ? (finalTranscript += t) : (interim = t);
    }
    input.value = finalTranscript || interim;
  };
  activeRecognizer.onerror = (e) => {
    setVoiceStatus(`Voice error: ${e.error}`, 3000);
    endActiveListening();
  };
  activeRecognizer.onend = () => {
    const heard = finalTranscript.trim() || input.value.trim();
    endActiveListening();
    if (heard) send(heard);
    else {
      setVoiceStatus("Nothing heard.", 2000);
      input.value = "";
    }
  };
  try {
    activeRecognizer.start();
  } catch (err) {
    setVoiceStatus("Microphone access denied.", 3000);
    endActiveListening();
  }
}

function endActiveListening() {
  isListeningActive = false;
  micBtn.classList.remove("listening");
  input.placeholder = "Speak or type — say 'Hey ARIA' to activate voice...";
  setVoiceStatus("");
  setTimeout(startWakeListener, 500);
}

micBtn.addEventListener("click", () => {
  if (!hasSTT) return;
  if (isListeningActive) {
    try {
      activeRecognizer?.stop();
    } catch (_) {}
    endActiveListening();
  } else {
    stopWakeListener();
    activateVoiceCommand();
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Audio feedback beep
// ─────────────────────────────────────────────────────────────────────────────

function playBeep(freq = 880, duration = 80) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator(),
      gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = freq;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(
      0.001,
      ctx.currentTime + duration / 1000,
    );
    osc.start();
    osc.stop(ctx.currentTime + duration / 1000);
  } catch (_) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// DOM helpers
// ─────────────────────────────────────────────────────────────────────────────

function appendMessage(role, text) {
  const now = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const label = role === "aria" ? "AR" : "ME";
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.innerHTML = `
    <div class="msg-avatar">${label}</div>
    <div>
      <div class="msg-body">${formatText(text)}</div>
      <div class="msg-time">${now}</div>
    </div>`;
  chat.appendChild(el);
  if (role === "aria") addCopyButtons(el.querySelector(".msg-body"));
  scrollBottom();
}

function appendImageMessage(name, b64, mime, caption) {
  const now = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const el = document.createElement("div");
  el.className = "msg aria";
  el.innerHTML = `
    <div class="msg-avatar">AR</div>
    <div>
      <div class="msg-body" style="padding:8px">
        <img src="data:${escHtml(mime)};base64,${b64}"
             alt="${escHtml(name)}"
             style="max-width:100%;max-height:320px;border-radius:2px;
                    border:1px solid var(--border);display:block;margin-bottom:6px">
        ${caption ? `<div style="font-size:.82rem;color:var(--text-dim)">${formatText(caption)}</div>` : ""}
      </div>
      <div class="msg-time">${now}</div>
    </div>`;
  chat.appendChild(el);
  scrollBottom();
}

function appendStreamMessage() {
  const wrapper = document.createElement("div");
  wrapper.className = "msg aria";
  const body = document.createElement("div");
  body.className = "msg-body";
  wrapper.innerHTML = `<div class="msg-avatar">AR</div>`;
  wrapper.appendChild(body);
  chat.appendChild(wrapper);
  scrollBottom();
  return body;
}

function showTyping() {
  if (typingEl) return;
  typingEl = document.createElement("div");
  typingEl.className = "msg aria";
  typingEl.innerHTML = `<div class="msg-avatar">AR</div>
    <div class="msg-body typing-indicator"><span></span><span></span><span></span></div>`;
  chat.appendChild(typingEl);
  scrollBottom();
}
function hideTyping() {
  if (typingEl) {
    typingEl.remove();
    typingEl = null;
  }
}
function scrollBottom() {
  chat.scrollTop = chat.scrollHeight;
}
function setStatus(online) {
  statusDot.className = "status-dot" + (online ? "" : " offline");
  statusText.textContent = online ? "ONLINE" : "RECONNECTING...";
}

let _vsTimer = null;
function setVoiceStatus(msg, hideAfterMs = 0) {
  voiceStat.textContent = msg;
  voiceStat.classList.toggle("visible", !!msg);
  clearTimeout(_vsTimer);
  if (hideAfterMs)
    _vsTimer = setTimeout(() => {
      voiceStat.textContent = "";
      voiceStat.classList.remove("visible");
    }, hideAfterMs);
}

// ─────────────────────────────────────────────────────────────────────────────
// Text formatting
// ─────────────────────────────────────────────────────────────────────────────

function formatText(raw) {
  let t = raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  t = t.replace(/```([\s\S]+?)```/g, "<pre>$1</pre>");
  t = t.replace(/`(.+?)`/g, "<code>$1</code>");
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/\*(.+?)\*/g, "<em>$1</em>");
  t = t.replace(
    /(https?:\/\/[^\s&<>"']+?)([.,;:!?)\]]*(?=\s|&|<|$))/g,
    (_, url, trail) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${trail}`,
  );
  t = t.replace(/\n/g, "<br>");
  return t;
}

function stripMarkdown(raw) {
  return raw
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/```[\s\S]+?```/g, "code block.")
    .replace(/`(.+?)`/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/\*Source:.*$/gm, "")
    .replace(/[#•─\[\]]+/g, "")
    .replace(/\n+/g, ". ")
    .replace(/\.{2,}/g, ".")
    .trim();
}

// ─────────────────────────────────────────────────────────────────────────────
// Copy-button CSS (injected once)
// ─────────────────────────────────────────────────────────────────────────────

(function injectCopyBtnStyles() {
  const s = document.createElement("style");
  s.textContent = `
    .copy-btn {
      position: absolute; top: 6px; right: 6px;
      background: var(--panel); border: 1px solid var(--border);
      color: var(--text-dim); font-family: "Share Tech Mono", monospace;
      font-size: .65rem; padding: 2px 8px; cursor: pointer;
      border-radius: 2px; opacity: 0;
      transition: opacity .15s, border-color .15s, color .15s;
    }
    pre:hover .copy-btn { opacity: 1; }
    .copy-btn:hover { border-color: var(--accent2); color: var(--accent); }
    @keyframes spin { to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(s);
})();

// ─────────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────────

// Keyboard shortcuts
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault();
    input.focus();
  }
  if (e.key === "Escape") stopSpeaking();
  if ((e.ctrlKey || e.metaKey) && e.key === "m") {
    e.preventDefault();
    muteBtn.click();
  }
});

loadPrefs().then(() => {
  injectUploadButton();
  connect();
  initTTS().then(() => {
    if (hasSTT)
      setTimeout(() => {
        startWakeListener();
        setVoiceStatus('👂 Listening for "Hey ARIA"...', 3000);
      }, 1200);
  });
});
