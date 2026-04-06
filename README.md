<div align="center">

# ⬡ ARIA

### Adaptive Reasoning & Intelligent Assistant

_A local, Jarvis-like AI assistant — no cloud, no API keys, no ML models required._

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi&logoColor=white)
![WebSocket](https://img.shields.io/badge/WebSocket-realtime-00d4ff?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![No API Key](https://img.shields.io/badge/API%20key-not%20required-brightgreen?style=flat-square)

</div>

---

## What is ARIA?

ARIA is a fully self-contained AI assistant that runs on your machine. It answers questions, searches the web, manages your files, opens apps, and remembers things about you — all from a sci-fi holographic web interface.

- **No API keys** — uses DuckDuckGo and Wikipedia's free public endpoints
- **No ML models** — fast rule-based NLP engine, zero GPU required
- **Persistent memory** — remembers your name and notes across sessions
- **Real-time** — WebSocket-powered, instant responses with typing indicator
- **Extensible** — adding a new command is just writing one Python function

---

## Preview

```
ARIA online. All systems operational. How can I assist you?

> Who invented the telephone?
  Alexander Graham Bell — Scottish-born inventor credited with patenting
  the first practical telephone in 1876...
  Source: Wikipedia — https://en.wikipedia.org/wiki/Alexander_Graham_Bell

> Calculate sqrt(1764)
  The result of sqrt(1764) is 42.

> My name is Alex
  Got it. I'll call you Alex from now on.

> Open calculator
  Opening calculator...
```

---

## Features

| Category            | Commands                                                                                               |
| ------------------- | ------------------------------------------------------------------------------------------------------ |
| 🌐 **Web Search**   | `search for black holes` · `google best Python frameworks`                                             |
| 📖 **Wikipedia**    | `wiki Alan Turing` · `What is quantum computing?` · `Who was Cleopatra?`                               |
| 🤖 **Smart Q&A**    | Just ask anything — ARIA auto-searches if no rule matches                                              |
| 🕐 **Time & Date**  | `What time is it?` · `What day is today?`                                                              |
| 🔢 **Calculator**   | `Calculate 15 * 8` · `sqrt(144)` · `2 ** 32` · `sin(pi/2)`                                             |
| 📁 **Files**        | `List files` · `Read file notes.txt` · `Create file todo.txt with content ...` · `Delete file old.txt` |
| 🖥️ **Open Apps**    | `Open calculator` · `Open browser` · `Open terminal` · `Open notepad`                                  |
| 💻 **System Info**  | `System info` — OS, processor, Python version, hostname                                                |
| 🧠 **Memory**       | `My name is Alex` · `Remember that deadline is Friday` · `What do you remember?` · `Clear memory`      |
| 💬 **Conversation** | Greetings · thanks · session duration · history                                                        |

---

## Getting Started

### Requirements

- Python **3.10** or higher
- pip

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

That's it. No `.env`, no API keys, no configuration needed.

---

## Project Structure

```
aria/
├── backend/
│   ├── main.py          # FastAPI server · WebSocket endpoint · static file serving
│   ├── aria_brain.py    # NLP engine · intent handlers · search fallback
│   ├── memory.py        # Session history + persistent facts (aria_memory.json)
│   ├── search.py        # DuckDuckGo instant answers · Wikipedia REST API
│   ├── tools.py         # Time · calculations · file CRUD · open apps · system info
│   ├── aria_memory.json # Auto-created on first run · stores your name & notes
│   └── requirements.txt
├── frontend/
│   ├── index.html       # Sci-fi holographic UI · quick-command buttons
│   ├── style.css        # Dark theme · cyan glow · animated logo · responsive
│   └── aria.js          # WebSocket client · markdown renderer · auto-reconnect
├── .gitignore
└── README.md
```

---

## How ARIA Answers Questions

ARIA processes every message through an ordered pipeline of intent handlers:

```
User input
    │
    ├─ Greeting / Identity / Name / Farewell / Thanks
    ├─ Time · Date · Calculations
    ├─ File operations · Open apps · System info
    ├─ Memory commands
    ├─ Explicit: "wiki ..." / "search for ..."
    └─ Smart fallback → auto-searches DuckDuckGo + Wikipedia
```

The smart fallback at the end means ARIA can answer almost any factual question — "Who is Marie Curie?", "What is the speed of light?", "How does photosynthesis work?" — without any special command.

---

## Adding New Commands

Open `backend/aria_brain.py` and add a handler function, then register it in `HANDLERS`:

```python
def _handle_joke(text: str, memory: Memory) -> str | None:
    if not _contains(text, "joke", "funny", "make me laugh"):
        return None
    return "Why do programmers prefer dark mode? Because light attracts bugs. 🐛"

HANDLERS = [
    ...
    _handle_joke,   # add before _handle_question_fallback
    ...
]
```

Each handler receives the lowercased input and the memory object. Return a string to respond, or `None` to pass to the next handler.

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

The file is created on first use and survives server restarts. Conversation history is session-only (not persisted — it would grow forever). Say **"clear memory"** to wipe the file and start fresh.

---

## Roadmap

- [ ] Voice input (Web Speech API)
- [ ] Voice output (text-to-speech)
- [ ] Ollama integration — plug in a local LLM (llama3, mistral…) as the final fallback
- [ ] Persistent conversation history with search
- [ ] Plugin system for custom skills
- [ ] Dark/light theme toggle

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

---

## License

MIT — do whatever you want with it.

---

<div align="center">
  <sub>Built with Python · FastAPI · DuckDuckGo · Wikipedia · zero dependencies on AI APIs</sub>
</div>
