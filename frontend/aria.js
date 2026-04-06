const chat = document.getElementById("chat");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

let ws = null;
let typingEl = null;

// ── WebSocket ──────────────────────────────────────────────────────────────

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    setStatus(true);
  };

  ws.onclose = () => {
    setStatus(false);
    setTimeout(connect, 3000); // auto-reconnect
  };

  ws.onerror = () => {
    setStatus(false);
  };

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);

    if (data.type === "typing") {
      showTyping();
    } else if (data.type === "aria") {
      hideTyping();
      appendMessage("aria", data.text);
    }
    // user messages echoed back from server are ignored here
    // (we already appended them optimistically on send)
  };
}

// ── Send ───────────────────────────────────────────────────────────────────

function send(text) {
  text = (text || input.value).trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

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

// Quick command buttons
document.querySelectorAll(".qcmd").forEach((btn) => {
  btn.addEventListener("click", () => send(btn.dataset.cmd));
});

// ── DOM helpers ────────────────────────────────────────────────────────────

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
    </div>
  `;
  chat.appendChild(el);
  scrollBottom();
}

function showTyping() {
  if (typingEl) return;
  typingEl = document.createElement("div");
  typingEl.className = "msg aria";
  typingEl.innerHTML = `
    <div class="msg-avatar">AR</div>
    <div class="msg-body typing-indicator">
      <span></span><span></span><span></span>
    </div>
  `;
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

// ── Text formatting (minimal markdown) ────────────────────────────────────

function formatText(raw) {
  // 1. Escape HTML
  let text = raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // 2. Fenced code blocks first (so URLs inside aren't linkified)
  text = text.replace(/```([\s\S]+?)```/g, "<pre>$1</pre>");

  // 3. Inline code
  text = text.replace(/`(.+?)`/g, "<code>$1</code>");

  // 4. Bold / italic
  text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // 5. Clickable URLs — strips trailing punctuation like . , ) gracefully
  text = text.replace(
    /(https?:\/\/[^\s&<>"']+?)([.,;:!?)\]]*(?=\s|&|<|$))/g,
    (_, url, trail) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${trail}`,
  );

  // 6. Newlines to <br>
  text = text.replace(/\n/g, "<br>");

  return text;
}

// ── Start ──────────────────────────────────────────────────────────────────

connect();
