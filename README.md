<div align="center">

# ⬡ ARIA

### Adaptive Reasoning & Intelligent Assistant

_A local, Jarvis-like AI assistant — no cloud, no API keys required._

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-realtime-00d4ff?style=flat-square)
![Ollama](https://img.shields.io/badge/Ollama-local%20LLM-black?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![No API Key](https://img.shields.io/badge/API%20key-not%20required-brightgreen?style=flat-square)

</div>

---

## What is ARIA?

ARIA is a fully self-contained AI assistant that runs on your machine. It answers questions, searches the web, manages your files, opens apps, remembers things about you — and when nothing else fits, falls back to a **local LLM via Ollama** for real reasoning. All from a sci-fi holographic web interface.

- **No cloud, no API keys** — uses DuckDuckGo, Wikipedia, and your own local models
- **Local LLM fallback** — integrates with [Ollama](https://ollama.com) (Mistral, Llama 3, Phi-3, and more)
- **Rule-based fast lane** — common intents (time, files, search, math) are answered instantly without hitting the LLM
- **Kokoro TTS** — natural, local text-to-speech with browser fallback
- **Wake word** — say "Hey ARIA" to activate voice input hands-free
- **Persistent memory** — remembers your name and notes across restarts
- **Real-time** — WebSocket-powered with typing indicators
- **Extensible** — adding a command is one Python function; adding a plugin is dropping a file

---

## Preview

```
ARIA online. All systems operational. How can I assist you?

> Who invented the telephone?
  Alexander Graham Bell — Scottish-born inventor credited with patenting
  the first practical telephone in 1876...
  Source: Wikipedia

> Calculate sqrt(1764)
  The result of sqrt(1764) is 42.

> Write me a Python function that checks if a number is prime
  def is_prime(n: int) -> bool:
      if n < 2: return False
      for i in range(2, int(n**0.5) + 1):
          if n % i == 0: return False
      return True

> Ollama status
  Ollama is online.
  Active model: mistral:latest
  Pulled models:
    - mistral:latest
    - llama3:latest
```

---

## Features

| Category            | Commands                                                                         |
| ------------------- | -------------------------------------------------------------------------------- |
| 🤖 **Local LLM**    | Free-form chat, code generation, reasoning — powered by Ollama                   |
| 🌐 **Web Search**   | `search for black holes` · `google best Python frameworks`                       |
| 📖 **Wikipedia**    | `wiki Alan Turing` · `What is quantum computing?` · `Who was Cleopatra?`         |
| 🕐 **Time & Date**  | `What time is it?` · `What day is today?`                                        |
| 🔢 **Calculator**   | `Calculate 15 * 8` · `sqrt(144)` · `2 ** 32` · `sin(pi/2)`                       |
| 📁 **Files**        | `List files` · `Read file notes.txt` · `Create file todo.txt with content ...`   |
| 🖥️ **Open Apps**    | `Open calculator` · `Open browser` · `Open terminal`                             |
| 💻 **System Info**  | `System info` — OS, processor, Python version, hostname                          |
| 🧠 **Memory**       | `My name is Alex` · `Remember that deadline is Friday` · `What do you remember?` |
| 💬 **Conversation** | Greetings · thanks · session duration · history                                  |
| 🔍 **LLM Status**   | `Ollama status` · `Which model are you using?`                                   |

---

## Getting Started

### Requirements

- Python **3.10** or higher
- pip
- (Optional) [Ollama](https://ollama.com/download) for local LLM support
- (Optional) `kokoro-onnx` + `soundfile` for natural TTS

### Install & Run

```bash
# Clone the repo
git clone https://github.com/your-username/aria.git
cd aria

# Install dependencies
pip install -r backend/requirements.txt

# Start ARIA
cd backend
uvicorn main:app --reload --port 8000
```

Then open **http://localhost:8000** in your browser.

That's it. ARIA works out of the box without Ollama or Kokoro. Both are optional upgrades.

---

## Enabling the Local LLM (Ollama)

Ollama gives ARIA real reasoning ability for anything the rule engine doesn't cover — creative writing, coding help, explanations, general conversation.

```bash
# 1. Install Ollama
#    https://ollama.com/download

# 2. Pull a model
ollama pull mistral        # recommended — fast 7B, great general responses

# 3. Start ARIA as normal — it detects Ollama automatically
cd backend && uvicorn main:app --reload --port 8000
```

The startup log will confirm: `Ollama online — active model: mistral`.

To change models, set `OLLAMA_MODEL` in your `.env` file (see [Configuration](#configuration)):

```bash
OLLAMA_MODEL=llama3
```

**Recommended models:**

| Model         | Size  | Good for                         |
| ------------- | ----- | -------------------------------- |
| `mistral`     | ~4 GB | General chat, default choice     |
| `llama3`      | ~5 GB | Strong reasoning, longer answers |
| `llama3.2`    | ~2 GB | Faster, still very capable       |
| `phi3`        | ~2 GB | Best on CPU-only machines        |
| `gemma2`      | ~5 GB | Great instruction following      |
| `deepseek-r1` | ~5 GB | Math and step-by-step reasoning  |

ARIA still works perfectly if Ollama is not running — it just uses web search and Wikipedia as its knowledge source instead.

---

## Enabling Natural TTS (Kokoro)

```bash
pip install kokoro-onnx soundfile
```

Model files (~85 MB) are downloaded automatically on first use. If Kokoro is not installed, ARIA falls back to your browser's built-in speech synthesis.

---

## Configuration

Copy `.env.example` to `.env` and edit as needed. No variables are required.

```bash
cp .env.example .env
```

| Variable       | Default                  | Description                   |
| -------------- | ------------------------ | ----------------------------- |
| `OLLAMA_HOST`  | `http://localhost:11434` | URL of your Ollama server     |
| `OLLAMA_MODEL` | `mistral`                | Model to use for LLM fallback |

---

## How ARIA Answers Questions

Every message runs through an ordered pipeline. The first handler that matches wins; the rest are skipped.

```
User input
    │
    ├─ Greeting / Identity / Name / Farewell / Thanks / How are you
    ├─ Time · Date
    ├─ Calculator (safe sandboxed eval via asteval)
    ├─ File operations · Open apps · System info
    ├─ Memory commands
    ├─ Explicit: "wiki ..." / "search for ..."
    ├─ Smart question fallback → Wikipedia + DuckDuckGo
    ├─ "Ollama status" / "use ollama ..." (force LLM)
    ├─ LLM fallback → Ollama (if running, with conversation history)
    └─ Unknown → friendly error with suggestions
```

The rule-based handlers at the top are instant. The LLM is only reached when nothing else matched, keeping response times fast for common tasks.

---

## Project Structure

```
aria/
├── backend/
│   ├── main.py          # FastAPI server · WebSocket · routes · session management
│   ├── aria_brain.py    # NLP pipeline · all intent handlers · LLM fallback
│   ├── ollama.py        # Ollama integration · prompt building · generate/stream
│   ├── memory.py        # Session history + persistent facts (aria_memory.json)
│   ├── search.py        # DuckDuckGo instant answers · Wikipedia REST API
│   ├── tools.py         # Time · safe calculator · file CRUD · open apps · system info
│   ├── tts.py           # Kokoro TTS · thread-safe engine loader · WAV synthesis
│   ├── aria_memory.json # Auto-created on first run · stores your name & notes
│   └── requirements.txt
├── frontend/
│   ├── index.html       # Sci-fi holographic UI · quick-command buttons
│   ├── style.css        # Dark theme · cyan glow · animated logo · responsive
│   └── aria.js          # WebSocket client · streaming support · TTS · STT · wake word
├── .env.example         # Documented environment variables
├── .gitignore
└── README.md
```

---

## Adding New Commands

Open `backend/aria_brain.py`, add a handler function, then register it in `HANDLERS`:

```python
def _handle_joke(text: str, memory: Memory) -> str | None:
    if not _contains(text, "joke", "funny", "make me laugh"):
        return None
    return "Why do programmers prefer dark mode? Because light attracts bugs. 🐛"

HANDLERS = [
    ...
    _handle_joke,   # add before _handle_llm_fallback
    ...
]
```

Each handler receives the lowercased input and the memory object. Return a string to respond, or `None` to pass to the next handler. The LLM fallback at the bottom means anything you don't explicitly handle will still get a sensible answer.

---

## API Endpoints

| Method | Path             | Description                                         |
| ------ | ---------------- | --------------------------------------------------- |
| `GET`  | `/`              | Serves the frontend                                 |
| `GET`  | `/health`        | Server status, TTS mode, LLM model, active sessions |
| `GET`  | `/tts/status`    | Whether Kokoro is available                         |
| `POST` | `/tts`           | Synthesize text → WAV audio                         |
| `GET`  | `/ollama/status` | Ollama availability, active model, pulled models    |
| `GET`  | `/ollama/models` | List of locally-pulled Ollama models                |
| `WS`   | `/ws`            | Main chat WebSocket                                 |

---

## Persistent Memory

ARIA saves facts to `backend/aria_memory.json` automatically:

```json
{
  "user_name": "Alex",
  "note_0": "project deadline is Friday",
  "note_1": "prefer dark mode interfaces"
}
```

The file is created on first use and survives server restarts. Say **"clear memory"** to wipe it and start fresh.

---

## Roadmap

- [x] Web search (DuckDuckGo + Wikipedia)
- [x] Voice input (Web Speech API + "Hey ARIA" wake word)
- [x] Voice output (Kokoro TTS + browser fallback)
- [x] Local LLM fallback (Ollama — Mistral, Llama 3, Phi-3…)
- [x] Conversation history passed to LLM
- [ ] Streaming LLM responses (token-by-token output)
- [ ] Persistent conversation history with search
- [ ] Plugin system for custom skills
- [ ] Typed memory facts (structured notes, deadlines, preferences)
- [ ] `/history` endpoint + conversation history panel in UI

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

---

## License

MIT — do whatever you want with it.

---

<div align="center">
  <sub>Built with Python · FastAPI · DuckDuckGo · Wikipedia · Ollama · zero cloud dependencies</sub>
</div>
