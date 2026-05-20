// ─────────────────────────────────────────────────────────────────────────────
// ARIA Frontend — File upload · Copy buttons · Persistent prefs · Ordered TTS
// ─────────────────────────────────────────────────────────────────────────────

const chat = document.getElementById("chat");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const stopBtn = document.getElementById("stop-btn");
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
  voice_mode: "wake",
  wake_enabled: true,
  wake_phrases: ["hey aria"],
  stt_model: "tiny.en",
  stt_silence_threshold: 0.035,
  stt_command_timeout: 8.0,
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
const MAX_AUDIO_QUEUE = 8;
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
    maybeResumeLocalWake();
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
      if (audioQueue.length >= MAX_AUDIO_QUEUE) audioQueue.shift();
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
  btn.innerHTML = `<span class="upload-fallback" aria-hidden="true">📎</span>
  <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66
             l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
  </svg>`;
  btn.setAttribute("aria-label", "Upload file");
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
let isStreaming = false;

function clientSessionId() {
  let id = localStorage.getItem("aria_client_session_id");
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    localStorage.setItem("aria_client_session_id", id);
  }
  return id;
}

function setBusy(active) {
  isStreaming = active;
  stopBtn.hidden = !active && !isSpeaking && !musicPlaying;
  if (active) stopWakeListener();
  else maybeResumeLocalWake();
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?client_session_id=${encodeURIComponent(clientSessionId())}`);
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
      setBusy(true);
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
      setBusy(false);
    } else if (data.type === "stream_cancelled") {
      hideTyping();
      stopSpeaking();
      if (currentStreamEl && !streamBuffer) currentStreamEl.parentElement.remove();
      currentStreamEl = null;
      streamBuffer = "";
      setBusy(false);
      setVoiceStatus("Stopped.", 1500);
    } else if (data.type === "error") {
      hideTyping();
      appendMessage("aria", `⚠ ${data.message || "Something went wrong."}`);
      setBusy(false);
    } else if (data.type === "action_pending") {
      hideTyping();
      appendActionCard(data.action);
    } else if (data.type === "file_image") {
      // Inline image from upload
      hideTyping();
      appendImageMessage(data.name, data.b64, data.mime, data.caption);
    } else if (data.type === "music_play") {
      stopSpeaking();
      createMusicPlayer(data.title, data.url, data.duration, data.thumbnail);
      setBusy(true);
    } else if (data.type === "music_stop") {
      destroyMusicPlayer(false);
      setBusy(false);
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
  setBusy(true);

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

function stopCurrent() {
  stopSpeaking();
  destroyMusicPlayer(false);
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop" }));
  }
  setBusy(false);
}

stopBtn.addEventListener("click", stopCurrent);

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

const hasLocalVoice = !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
let localRecorder = null,
  localRecordTimer = null,
  localVadTimer = null,
  localVadContext = null,
  localRecordStream = null,
  localWakeTimer = null,
  localWakeRecorder = null,
  localWakeStream = null,
  isListeningActive = false,
  isRecordingLocal = false,
  isWakeListeningLocal = false,
  micAccessGranted = false;
const WAKE_WORD = "hey aria";
const WAKE_ALTS = [
  "hey area",
  "hay aria",
  "hey arya",
  "hey ariya",
  "hi aria",
  "hi area",
  "aria",
];
const WAKE_TO_COMMAND_DELAY_MS = 250;
const COMMAND_LISTENING_MS = 8000;
const COMMAND_MIN_RECORDING_MS = 900;
const COMMAND_SILENCE_AFTER_SPEECH_MS = 900;
const COMMAND_SILENCE_WITHOUT_SPEECH_MS = 2600;
const COMMAND_VOICE_THRESHOLD = 0.035;
const WAKE_CHUNK_MS = 2500;
const LOCAL_STT_UNAVAILABLE_MSG =
  "Local transcription is not installed. Run: pip install faster-whisper";

async function requestMicAccess() {
  if (micAccessGranted) return true;
  if (!navigator.mediaDevices?.getUserMedia) {
    setVoiceStatus("Microphone access is not available in this browser.", 5000);
    return false;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
    micAccessGranted = true;
    return true;
  } catch (err) {
    const name = err?.name || "";
    const blocked =
      name === "NotAllowedError" || name === "PermissionDeniedError";
    setVoiceStatus(
      blocked
        ? "Microphone access blocked. Allow microphone access, then click the mic."
        : "Could not access the microphone. Check your input device and try again.",
      6000,
    );
    return false;
  }
}

if (!hasLocalVoice) {
  micBtn.title = "Voice input needs microphone recording support";
  micBtn.style.opacity = "0.4";
  micBtn.style.cursor = "not-allowed";
  setVoiceStatus("Voice input needs microphone recording support", 4000);
} else {
  micBtn.title = "Local voice input";
}

function preferredAudioMime() {
  const types = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/wav",
  ];
  return types.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function audioFilename(mimeType) {
  if (mimeType.includes("mp4")) return "speech.mp4";
  if (mimeType.includes("wav")) return "speech.wav";
  return "speech.webm";
}

async function transcribeBlob(blob, mimeType) {
  const form = new FormData();
  form.append("file", blob, audioFilename(mimeType));
  const res = await fetch("/stt", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || LOCAL_STT_UNAVAILABLE_MSG);
  return (data.text || "").trim();
}

function hasWakeWord(text) {
  const t = normalizeSpeechText(text);
  const phrases = Array.isArray(_prefs.wake_phrases) && _prefs.wake_phrases.length
    ? _prefs.wake_phrases
    : [WAKE_WORD, ...WAKE_ALTS];
  return (
    phrases.some((w) => t.includes(normalizeSpeechText(w))) ||
    WAKE_ALTS.some((w) => t.includes(normalizeSpeechText(w)))
  );
}

function normalizeSpeechText(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stripWakeWord(text) {
  let cleaned = text.trim();
  const phrases = Array.isArray(_prefs.wake_phrases) && _prefs.wake_phrases.length
    ? _prefs.wake_phrases
    : [WAKE_WORD];
  const wakePatterns = [...phrases, ...WAKE_ALTS].map((wake) =>
    wake
      .split(/\s+/)
      .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("[^a-z0-9]+"),
  );
  for (const pattern of wakePatterns) {
    const re = new RegExp(`^\\s*${pattern}\\b\\s*`, "i");
    if (re.test(cleaned)) {
      cleaned = cleaned.replace(re, "").trim();
      break;
    }
  }
  return cleaned.replace(/^[,.:;!?-]+/, "").trim();
}

function stopCommandVad() {
  if (localVadTimer) {
    clearInterval(localVadTimer);
    localVadTimer = null;
  }
  if (localVadContext) {
    localVadContext.close().catch(() => {});
    localVadContext = null;
  }
}

function startCommandVad(stream) {
  stopCommandVad();
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    localVadContext = new AudioCtx();
    const source = localVadContext.createMediaStreamSource(stream);
    const analyser = localVadContext.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);

    const samples = new Uint8Array(analyser.fftSize);
    const startedAt = Date.now();
    let hasSpeech = false;
    let lastVoiceAt = startedAt;

    localVadTimer = setInterval(() => {
      if (!localRecorder || localRecorder.state !== "recording") return;
      analyser.getByteTimeDomainData(samples);
      let sumSquares = 0;
      for (const sample of samples) {
        const centered = (sample - 128) / 128;
        sumSquares += centered * centered;
      }
      const rms = Math.sqrt(sumSquares / samples.length);
      const now = Date.now();
      if (rms > Number(_prefs.stt_silence_threshold || COMMAND_VOICE_THRESHOLD)) {
        hasSpeech = true;
        lastVoiceAt = now;
      }

      const elapsed = now - startedAt;
      const silenceMs = now - lastVoiceAt;
      if (
        elapsed >= COMMAND_MIN_RECORDING_MS &&
        ((hasSpeech && silenceMs >= COMMAND_SILENCE_AFTER_SPEECH_MS) ||
          (!hasSpeech && elapsed >= COMMAND_SILENCE_WITHOUT_SPEECH_MS))
      ) {
        stopLocalRecording();
      }
    }, 120);
  } catch (_) {
    stopCommandVad();
  }
}

function stopLocalRecording() {
  if (localRecordTimer) {
    clearTimeout(localRecordTimer);
    localRecordTimer = null;
  }
  stopCommandVad();
  if (localRecorder && localRecorder.state !== "inactive") {
    try {
      localRecorder.stop();
    } catch (_) {}
  }
}

function stopWakeListener() {
  if (localWakeTimer) {
    clearTimeout(localWakeTimer);
    localWakeTimer = null;
  }
  if (localWakeRecorder && localWakeRecorder.state !== "inactive") {
    try {
      localWakeRecorder.stop();
    } catch (_) {}
  }
  localWakeStream?.getTracks().forEach((track) => track.stop());
  localWakeStream = null;
  localWakeRecorder = null;
  isWakeListeningLocal = false;
  micBtn.classList.remove("wake-active");
}

function maybeResumeLocalWake() {
  if (
    hasLocalVoice &&
    micAccessGranted &&
    !isWakeListeningLocal &&
    !isListeningActive &&
    !isRecordingLocal &&
    !isStreaming &&
    !isSpeaking &&
    !musicPlaying
  ) {
    setTimeout(startWakeListener, 500);
  }
}

async function startWakeListener() {
  if (
    !hasLocalVoice ||
    isWakeListeningLocal ||
    _prefs.wake_enabled === false ||
    _prefs.voice_mode === "push_to_talk" ||
    isListeningActive ||
    isStreaming ||
    isSpeaking ||
    musicPlaying
  )
    return false;
  if (!(await requestMicAccess())) return false;

  isWakeListeningLocal = true;
  micBtn.classList.add("wake-active");
  input.placeholder = "Speak or type - say 'Hey ARIA'...";
  setVoiceStatus('Local wake word active: say "Hey ARIA"', 3000);
  listenForWakeChunk();
  return true;
}

async function listenForWakeChunk() {
  if (!isWakeListeningLocal || isListeningActive) return;

  let chunks = [];
  let mimeType = "";
  try {
    localWakeStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mimeType = preferredAudioMime();
    localWakeRecorder = new MediaRecorder(
      localWakeStream,
      mimeType ? { mimeType } : undefined,
    );
    localWakeRecorder.ondataavailable = (event) => {
      if (event.data?.size) chunks.push(event.data);
    };
    localWakeRecorder.onstop = async () => {
      const stream = localWakeStream;
      localWakeStream = null;
      stream?.getTracks().forEach((track) => track.stop());
      localWakeRecorder = null;

      if (!isWakeListeningLocal || isListeningActive) return;
      const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
      chunks = [];
      if (!blob.size) {
        localWakeTimer = setTimeout(listenForWakeChunk, 250);
        return;
      }

      try {
        const text = await transcribeBlob(blob, mimeType);
        if (hasWakeWord(text)) {
          const command = stripWakeWord(text);
          stopWakeListener();
          if (command) {
            input.value = "";
            send(command);
          } else {
            setTimeout(startLocalVoiceCommand, WAKE_TO_COMMAND_DELAY_MS);
          }
          return;
        }
      } catch (err) {
        stopWakeListener();
        setVoiceStatus(err.message || LOCAL_STT_UNAVAILABLE_MSG);
        return;
      }
      localWakeTimer = setTimeout(listenForWakeChunk, 250);
    };
    localWakeRecorder.start();
    localWakeTimer = setTimeout(() => {
      if (localWakeRecorder?.state === "recording") localWakeRecorder.stop();
    }, WAKE_CHUNK_MS);
  } catch (err) {
    stopWakeListener();
    const blocked =
      err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError";
    setVoiceStatus(
      blocked
        ? "Microphone access blocked. Allow microphone access, then click the mic."
        : "Could not access the microphone. Check your input device and try again.",
      6000,
    );
  }
}

async function startLocalVoiceCommand() {
  if (!hasLocalVoice || isRecordingLocal) return;
  if (!(await requestMicAccess())) return;

  let chunks = [];
  try {
    stopWakeListener();
    localRecordStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    micAccessGranted = true;
    const mimeType = preferredAudioMime();
    localRecorder = new MediaRecorder(
      localRecordStream,
      mimeType ? { mimeType } : undefined,
    );

    localRecorder.ondataavailable = (event) => {
      if (event.data?.size) chunks.push(event.data);
    };

    localRecorder.onerror = () => {
      setVoiceStatus("Could not record microphone audio.", 5000);
    };

    localRecorder.onstop = async () => {
      const stream = localRecordStream;
      localRecordStream = null;
      stream?.getTracks().forEach((track) => track.stop());
      localRecorder = null;
      stopCommandVad();
      isRecordingLocal = false;
      isListeningActive = false;
      micBtn.classList.remove("listening");
      input.placeholder = "Speak or type - say 'Hey ARIA'...";

      const blob = new Blob(chunks, { type: mimeType || "audio/webm" });
      chunks = [];
      if (!blob.size) {
        setVoiceStatus("Nothing recorded.", 2500);
        return;
      }

      setVoiceStatus("Transcribing locally...");
      try {
        const heard = await transcribeBlob(blob, mimeType);
        if (heard) {
          input.value = "";
          send(heard);
        } else {
          setVoiceStatus("Nothing heard.", 2500);
        }
      } catch (err) {
        setVoiceStatus(err.message || "Could not reach local transcription.", 7000);
      } finally {
        setTimeout(startWakeListener, 500);
      }
    };

    _ttsReady = true;
    stopSpeaking();
    playBeep(880, 80);
    isRecordingLocal = true;
    isListeningActive = true;
    micBtn.classList.remove("unavailable");
    micBtn.classList.add("listening");
    input.placeholder = "Listening...";
    setVoiceStatus("Recording for local transcription...");
    localRecorder.start();
    startCommandVad(localRecordStream);
    const timeoutMs = Math.max(1, Number(_prefs.stt_command_timeout || 8)) * 1000;
    localRecordTimer = setTimeout(stopLocalRecording, timeoutMs || COMMAND_LISTENING_MS);
  } catch (err) {
    stopCommandVad();
    localRecordStream?.getTracks().forEach((track) => track.stop());
    localRecordStream = null;
    localRecorder = null;
    isRecordingLocal = false;
    isListeningActive = false;
    micBtn.classList.remove("listening");
    const blocked =
      err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError";
    setVoiceStatus(
      blocked
        ? "Microphone access blocked. Allow microphone access, then click the mic."
        : "Could not access the microphone. Check your input device and try again.",
      6000,
    );
    setTimeout(startWakeListener, 500);
  }
}

micBtn.addEventListener("click", async () => {
  if (!hasLocalVoice) return;
  if (isRecordingLocal) {
    stopLocalRecording();
    return;
  }
  if (isWakeListeningLocal) {
    stopWakeListener();
    await startLocalVoiceCommand();
    return;
  }
  if (_prefs.voice_mode === "push_to_talk" || _prefs.wake_enabled === false) {
    await startLocalVoiceCommand();
  } else {
    await startWakeListener();
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

function appendActionCard(action) {
  if (!action) return;
  const now = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const el = document.createElement("div");
  el.className = "msg aria";
  el.innerHTML = `
    <div class="msg-avatar">AR</div>
    <div>
      <div class="msg-body action-card" data-action-id="${escHtml(action.id)}">
        <div style="font-size:.72rem;color:var(--accent2);text-transform:uppercase;letter-spacing:.08em">Action pending</div>
        <div style="margin:6px 0 8px">${escHtml(action.summary || action.type)}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button data-action="approve">Approve</button>
          <button data-action="reject">Reject</button>
        </div>
      </div>
      <div class="msg-time">${now}</div>
    </div>`;
  const body = el.querySelector(".action-card");
  body.querySelector("[data-action='approve']").addEventListener("click", () =>
    resolveAction(action.id, "approve", body),
  );
  body.querySelector("[data-action='reject']").addEventListener("click", () =>
    resolveAction(action.id, "reject", body),
  );
  chat.appendChild(el);
  scrollBottom();
}

async function resolveAction(id, decision, card) {
  try {
    const res = await fetch(`/actions/${encodeURIComponent(id)}/${decision}`, {
      method: "POST",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Action failed");
    const result = data.result ? `<div style="margin-top:8px">${formatText(data.result)}</div>` : "";
    card.innerHTML = `
      <div style="font-size:.72rem;color:var(--accent2);text-transform:uppercase;letter-spacing:.08em">Action ${data.status}</div>
      <div style="margin-top:6px">${escHtml(data.summary || data.type)}</div>
      ${result}`;
  } catch (err) {
    setVoiceStatus(err.message || "Action failed", 4000);
  }
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
    if (hasLocalVoice)
      setTimeout(() => {
        setVoiceStatus('Click the mic once to enable local "Hey ARIA".', 5000);
      }, 1200);
  });
});
