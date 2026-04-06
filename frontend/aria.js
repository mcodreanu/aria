// ─────────────────────────────────────────────────────────────────────────────
// ARIA Frontend
// TTS:   Kokoro-ONNX via POST /tts  (primary, natural voice)
//        Web SpeechSynthesis        (fallback if Kokoro unavailable)
// STT:   Web Speech API             (Chrome/Edge only)
// Wake:  "Hey ARIA" passive listener
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
// TTS mode detection — check once if Kokoro backend is available
// ─────────────────────────────────────────────────────────────────────────────

let kokoroAvailable = false;
let currentAudio = null;
let isMuted = false;
let audioQueue = [];
let isSpeaking = false;

// FIX: was called twice — once standalone (promise ignored) and once with .then().
// Now there is exactly one call at boot, inside the .then() so the wake listener
// starts only after TTS detection has resolved.
async function detectTTSMode() {
  try {
    const res = await fetch("/tts/status");
    const data = await res.json();
    kokoroAvailable = data.available === true;
  } catch (_) {
    kokoroAvailable = false;
  }

  if (kokoroAvailable) {
    setVoiceStatus("🎙 Kokoro voice engine ready", 3000);
  } else {
    setVoiceStatus("⚠ Kokoro not installed — using browser voice", 4000);
    loadBrowserVoices();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Kokoro TTS  (primary)
// ─────────────────────────────────────────────────────────────────────────────

async function speakKokoro(text) {
  if (isMuted || !text.trim()) return;

  const capped = text.length > 500 ? text.slice(0, 497) + "..." : text;

  try {
    const res = await fetch("/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: capped }),
    });

    if (!res.ok) throw new Error(`TTS status ${res.status}`);

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);

    currentAudio = audio;
    micBtn.classList.add("speaking");

    audio.onended = () => {
      URL.revokeObjectURL(url);
      micBtn.classList.remove("speaking");
      currentAudio = null;
      isSpeaking = false;
      _drainQueue();
    };

    audio.onerror = () => {
      URL.revokeObjectURL(url);
      micBtn.classList.remove("speaking");
      currentAudio = null;
      isSpeaking = false;
      _drainQueue();
    };

    isSpeaking = true;
    await audio.play();
  } catch (err) {
    console.warn("[TTS] Kokoro failed, falling back to browser:", err);
    isSpeaking = false;
    micBtn.classList.remove("speaking");
    speakBrowser(capped);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Browser TTS  (fallback)
// ─────────────────────────────────────────────────────────────────────────────

let browserVoice = null;

function loadBrowserVoices() {
  const tryLoad = () => {
    const voices = speechSynthesis.getVoices();
    if (!voices.length) return;

    const priority = [
      (v) => v.name.includes("Zira"),
      (v) => v.name.includes("Jenny"),
      (v) => v.name.includes("Aria"),
      (v) => v.name.includes("Samantha"),
      (v) => v.name.includes("Karen"),
      (v) => v.name.includes("Google UK English Female"),
      (v) => /female/i.test(v.name) && v.lang.startsWith("en"),
      (v) => v.lang === "en-US",
      (v) => v.lang.startsWith("en"),
    ];
    for (const match of priority) {
      const v = voices.find(match);
      if (v) {
        browserVoice = v;
        break;
      }
    }
  };
  speechSynthesis.onvoiceschanged = tryLoad;
  tryLoad();
}

function speakBrowser(text) {
  if (isMuted || !text.trim()) return;
  speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  if (browserVoice) utt.voice = browserVoice;
  utt.rate = 0.95;
  utt.pitch = 1.05;
  utt.lang = "en-US";
  utt.onstart = () => micBtn.classList.add("speaking");
  utt.onend = () => {
    micBtn.classList.remove("speaking");
    isSpeaking = false;
    _drainQueue();
  };
  utt.onerror = () => {
    micBtn.classList.remove("speaking");
    isSpeaking = false;
    _drainQueue();
  };
  isSpeaking = true;
  speechSynthesis.speak(utt);
}

// ─────────────────────────────────────────────────────────────────────────────
// Unified speak() — routes to Kokoro or browser, with queue
// ─────────────────────────────────────────────────────────────────────────────

function speak(text) {
  const clean = stripMarkdown(text);
  if (!clean.trim()) return;

  if (isSpeaking) {
    audioQueue.push(clean);
    return;
  }

  _doSpeak(clean);
}

function _doSpeak(text) {
  if (kokoroAvailable) {
    speakKokoro(text);
  } else {
    speakBrowser(text);
  }
}

function _drainQueue() {
  if (audioQueue.length > 0 && !isMuted) {
    const next = audioQueue.shift();
    setTimeout(() => _doSpeak(next), 120);
  }
}

function stopSpeaking() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  speechSynthesis.cancel();
  audioQueue = [];
  isSpeaking = false;
  micBtn.classList.remove("speaking");
}

// ─────────────────────────────────────────────────────────────────────────────
// Mute toggle
// ─────────────────────────────────────────────────────────────────────────────

const ICON_SPEAKER_ON = `
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
    <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
    <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
  </svg>`;

const ICON_SPEAKER_OFF = `
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
    <line x1="23" y1="9" x2="17" y2="15"/>
    <line x1="17" y1="9" x2="23" y2="15"/>
  </svg>`;

muteBtn.innerHTML = ICON_SPEAKER_ON;

muteBtn.addEventListener("click", () => {
  isMuted = !isMuted;
  muteBtn.classList.toggle("muted", isMuted);
  muteBtn.innerHTML = isMuted ? ICON_SPEAKER_OFF : ICON_SPEAKER_ON;
  muteBtn.title = isMuted ? "Unmute voice" : "Mute voice";
  if (isMuted) {
    stopSpeaking();
    setVoiceStatus("🔇 Voice muted", 2000);
  } else {
    setVoiceStatus("🔊 Voice on", 2000);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket
// ─────────────────────────────────────────────────────────────────────────────

// Streaming state
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
      // Non-streaming full response (existing behavior)
      hideTyping();
      appendMessage("aria", data.text);
      speak(data.text);

      // --- Streaming support ---
    } else if (data.type === "stream_start") {
      hideTyping();
      streamBuffer = "";
      currentStreamEl = appendStreamMessage();
    } else if (data.type === "stream_chunk") {
      streamBuffer += data.text;
      if (currentStreamEl) {
        currentStreamEl.innerHTML = formatText(streamBuffer);
        scrollBottom();
      }
    } else if (data.type === "stream_end") {
      if (currentStreamEl) {
        // Stamp a timestamp
        const timeEl = document.createElement("div");
        timeEl.className = "msg-time";
        timeEl.textContent = new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });
        currentStreamEl.parentElement.appendChild(timeEl);
        speak(streamBuffer);
        currentStreamEl = null;
        streamBuffer = "";
      }
    }
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Send
// ─────────────────────────────────────────────────────────────────────────────

function send(text) {
  text = (text || input.value).trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  stopSpeaking();
  appendMessage("user", text);
  ws.send(JSON.stringify({ text }));
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

let wakeRecognizer = null;
let activeRecognizer = null;
let isListeningActive = false;
let wakeEnabled = hasSTT;

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
    for (let i = event.resultIndex; i < event.results.length; i++) {
      for (let a = 0; a < event.results[i].length; a++) {
        const alt = event.results[i][a].transcript.toLowerCase().trim();
        if (alt.includes(WAKE_WORD) || WAKE_ALTS.some((w) => alt.includes(w))) {
          stopWakeListener();
          activateVoiceCommand();
          return;
        }
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
// Audio feedback
// ─────────────────────────────────────────────────────────────────────────────

function playBeep(freq = 880, duration = 80) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
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
  scrollBottom();
}

/**
 * Creates an ARIA message bubble whose .msg-body can be updated incrementally
 * during streaming. Returns the .msg-body element for direct mutation.
 */
function appendStreamMessage() {
  const label = "AR";
  const wrapper = document.createElement("div");
  wrapper.className = "msg aria";
  const body = document.createElement("div");
  body.className = "msg-body";
  wrapper.innerHTML = `<div class="msg-avatar">${label}</div>`;
  wrapper.appendChild(body);
  chat.appendChild(wrapper);
  scrollBottom();
  return body;
}

function showTyping() {
  if (typingEl) return;
  typingEl = document.createElement("div");
  typingEl.className = "msg aria";
  typingEl.innerHTML = `
    <div class="msg-avatar">AR</div>
    <div class="msg-body typing-indicator">
      <span></span><span></span><span></span>
    </div>`;
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
// Boot — single detectTTSMode() call, wake listener starts after it resolves
// ─────────────────────────────────────────────────────────────────────────────

connect();

detectTTSMode().then(() => {
  if (hasSTT) {
    setTimeout(() => {
      startWakeListener();
      setVoiceStatus('👂 Listening for "Hey ARIA"...', 3000);
    }, 1200);
  }
});
