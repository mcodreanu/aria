"""
ARIA Brain — Rule-based NLP engine with local LLM fallback.

Handler pipeline (in order):
  1. Fast rule-based handlers (greetings, time, files, search…)
  2. _handle_llm_fallback  — Ollama local model for anything unmatched
  3. _handle_unknown       — last resort if Ollama is also unavailable

Entry points:
  process(user_input, memory)         -> str
  process_stream(user_input, memory)  -> AsyncIterator[str]

Music sentinels returned by _handle_music():
  "MUSIC_PLAY:<query>"  — main.py runs yt-dlp in a thread, sends music_play WS event
  "MUSIC_STOP:"         — main.py sends music_stop WS event
"""

import re
import random
import logging
from typing import AsyncIterator
from memory import Memory
from tools import (
    get_time, get_date, get_day, calculate,
    list_directory, create_file_tool, read_file_tool, delete_file_tool,
    open_app, get_system_info,
)
from search import web_search, wikipedia_search_and_summarize
import ollama as ollama_engine
import sysinfo.music as music_engine
from sysinfo.weather import get_weather
from sysinfo.converter import convert
from sysinfo.clipboard_tool import handle_clipboard_read, handle_clipboard_write, handle_screenshot
import sysinfo.scheduler as scheduler_engine

logger = logging.getLogger("aria.brain")

MUSIC_PLAY_PREFIX = "MUSIC_PLAY:"
MUSIC_STOP_PREFIX = "MUSIC_STOP:"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contains(text: str, *keywords) -> bool:
    return any(k in text for k in keywords)

def _extract(text: str, pattern: str, group: int = 1):
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(group).strip() if m else None

# ---------------------------------------------------------------------------
# Existing intent handlers (unchanged)
# ---------------------------------------------------------------------------

def _handle_greeting(text, memory):
    greetings = ["hello","hi","hey","good morning","good evening",
                 "good afternoon","howdy","what's up","sup","greetings"]
    if not _contains(text, *greetings):
        return None
    name = memory.recall("user_name")
    return random.choice([
        f"Hello{', '+name if name else ''}. ARIA online and ready.",
        f"Hey{' '+name if name else ''}. All systems operational.",
        f"Greetings{', '+name if name else ''}. How can I assist you?",
        "Hello. I'm here. What do you need?",
    ])

def _handle_identity(text, memory):
    if not _contains(text,"who are you","what are you","your name",
                     "introduce yourself","what's aria","what is aria"):
        return None
    llm_line = ""
    if ollama_engine.is_available():
        llm_line = (f" I'm also backed by a local **{ollama_engine.active_model()}**"
                    " language model via Ollama for anything that needs real reasoning.")
    return (
        "I'm **ARIA** — Adaptive Reasoning & Intelligent Assistant. "
        "I run locally on your machine and can answer questions by searching "
        f"DuckDuckGo and Wikipedia, manage your files, do calculations, open apps, and more.{llm_line} "
        "Ask me anything."
    )

def _handle_name_learning(text, memory):
    for p in [r"my name is ([a-zA-Z]+)", r"i(?:'m| am) ([a-zA-Z]+)",
              r"call me ([a-zA-Z]+)", r"people call me ([a-zA-Z]+)"]:
        name = _extract(text, p)
        if name and len(name) > 1 and name.lower() not in {
                "a","an","the","aria","fine","good","ok","here"}:
            memory.remember("user_name", name.capitalize())
            return f"Got it. I'll call you **{name.capitalize()}** from now on."
    return None

def _handle_name_recall(text, memory):
    if not _contains(text,"my name","do you know me","what do you call me",
                     "who am i","remember me"):
        return None
    name = memory.recall("user_name")
    return f"Of course — you're **{name}**." if name else \
           "I don't know your name yet. You can tell me with 'My name is ...'."

def _handle_time(text, memory):
    if not _contains(text,"time","clock","hour"):
        return None
    return get_time()

def _handle_date(text, memory):
    if _contains(text,"what day","what's today","today's date","current date","what date"):
        return f"Today is **{get_date()}** ({get_day()})."
    return None

_CALC_OPERATORS_RE = re.compile(r"[\+\-\*\/\%\^]|sqrt|sin|cos|tan|log|exp|pow|ceil|floor")

def _handle_calculation(text, memory):
    explicit = ["calculate","compute","what is","what's","how much is","solve","evaluate"]
    expr = None
    if _contains(text, *explicit):
        expr = _extract(text, r"(?:calculate|compute|what(?:'s| is)|solve|evaluate)\s+(.+)", 1)
        if not expr:
            return None
    if expr is None:
        if not _CALC_OPERATORS_RE.search(text):
            return None
        candidate = _extract(text, r"([\d\s\.\+\-\*\/\%\^\(\)a-zA-Z_]+(?:\d[\d\s]*)?)", 1)
        if not candidate or not re.search(r"\d", candidate):
            return None
        expr = candidate
    expr = re.sub(r"[^\d\s\.\+\-\*\/\%\^\(\)a-zA-Z_]", "", expr).strip()
    if not re.search(r"\d", expr):
        return None
    return calculate(expr)

def _handle_files(text, memory):
    if _contains(text,"list files","show files","list directory",
                 "what's in","what is in","contents of","ls ","dir "):
        path = _extract(text, r"(?:in|of|directory|folder)\s+['\"]?([^\s'\"]+)['\"]?") or "."
        return list_directory(path)
    if _contains(text,"read file","open file","show file","contents of file","cat "):
        fname = _extract(text, r"(?:read|open|show|cat)\s+(?:file\s+)?['\"]?([^\s'\"]+\.\w+)['\"]?")
        return read_file_tool(fname) if fname else "Which file should I read? Specify a filename."
    if _contains(text,"create file","make file","new file","write file"):
        fname = _extract(text, r"(?:create|make|new|write)\s+(?:a\s+)?(?:file\s+)?['\"]?([^\s'\"]+\.\w+)['\"]?")
        content = _extract(text, r"(?:with content|containing|with text)\s+['\"]?(.+)['\"]?$")
        return create_file_tool(fname, content or "") if fname else "What should I name the file?"
    if _contains(text,"delete file","remove file","erase file"):
        fname = _extract(text, r"(?:delete|remove|erase)\s+(?:file\s+)?['\"]?([^\s'\"]+\.\w+)['\"]?")
        return delete_file_tool(fname) if fname else "Which file should I delete?"
    return None

def _handle_open_app(text, memory):
    if not _contains(text,"open","launch","start","run"):
        return None
    app = _extract(text, r"(?:open|launch|start|run)\s+(?:the\s+)?([a-zA-Z]+)")
    return open_app(app) if app else None

def _handle_system(text, memory):
    if _contains(text,"system info","system information","about this computer",
                 "my computer","hardware","os info","operating system"):
        return get_system_info()
    return None

def _handle_memory_commands(text, memory):
    if _contains(text,"remember that","don't forget","keep in mind","note that"):
        fact = _extract(text, r"(?:remember that|don't forget|keep in mind|note that)\s+(.+)")
        if fact:
            memory.remember(f"note_{len(memory.facts)}", fact)
            return f"Noted: *{fact}*"
    if _contains(text,"what do you remember","what do you know about me",
                 "what have you noted","show memory","your memory"):
        if not memory.facts:
            return "I haven't noted anything about you yet this session."
        lines = "\n".join(f"- **{k}**: {v}" for k,v in memory.facts.items()
                          if not k.startswith("_"))
        return f"Here's what I remember:\n{lines}" if lines else "Nothing noted yet."
    if _contains(text,"forget everything","clear memory","reset memory","forget all"):
        memory.reset()
        return "Memory cleared. Starting fresh."
    if _contains(text,"how long","session time","how long have we","session duration"):
        return f"We've been talking for **{memory.session_duration()}**."
    return None

def _handle_history(text, memory):
    if not _contains(text,"last message","what did i say","previous message",
                     "repeat that","what did you say"):
        return None
    last = memory.last_user_message()
    return f"Your last message was: *\"{last}\"*" if last else \
           "This is the beginning of our conversation."

def _handle_help(text, memory):
    if not _contains(text,"help","what can you do","commands","capabilities",
                     "features","abilities","instructions"):
        return None
    return """Here's what I can do:

**🌐 Web Search**
- "Search for black holes" / "Search the web for Python tutorials"

**📖 Wikipedia**
- "wiki Python" / "Who is Alan Turing?" / "What is quantum computing?"

**🕐 Time & Date**
- "What time is it?" / "What's today's date?"

**🔢 Calculations**
- "Calculate 15 * 8" / "What is sqrt(144)?" / "2 ** 10"

**📁 Files**
- "List files" / "Read file notes.txt" / "Create file todo.txt with content ..."

**🎵 Music**
- "Play Bohemian Rhapsody" / "Play some lofi hip hop"
- "Stop music" / "What's playing?"

**🌤 Weather**
- "What's the weather in Palma?" / "Weather in Tokyo" / "Is it raining in London?"

**💱 Conversions**
- "Convert 250 EUR to USD" / "15 miles to km" / "100 F to C" / "5 kg to lbs"

**📋 Clipboard**
- "Read my clipboard" / "What's in my clipboard?"
- "Copy [text] to clipboard"

**📸 Screenshot**
- "Take a screenshot" / "Capture my screen"

**⏰ Reminders**
- "Remind me in 20 minutes to check the oven"
- "Set a reminder for 1 hour — call mom"
- "Show my reminders" / "Cancel reminder [id]" / "Cancel all reminders"

**🖥 Apps**
- "Open calculator" / "Open browser" / "Open terminal"

**🧠 Memory**
- "My name is Alex" / "Remember that deadline is Friday" / "What do you remember?"

**💻 System**
- "System info"

**📜 History**
- "Search history for Python" / "Show my chat history" / "How many messages have we exchanged?"

**🤖 Local AI (Ollama)**
- Free-form chat, code generation, reasoning
- "use ollama [prompt]" to force LLM · "ollama status" to check model"""

def _handle_farewell(text, memory):
    if not _contains(text,"bye","goodbye","see you","exit","quit",
                     "shut down","goodnight","good night","farewell"):
        return None
    name = memory.recall("user_name")
    return random.choice([
        f"Goodbye{', '+name if name else ''}. ARIA standing by.",
        f"See you{', '+name if name else ''}. Shutting down conversational mode.",
        "Until next time. ARIA out.",
        f"Take care{', '+name if name else ''}.",
    ])

def _handle_thanks(text, memory):
    if not _contains(text,"thank","thanks","thx","ty","appreciate"):
        return None
    return random.choice(["You're welcome.","Of course.","Always.",
                          "Happy to help.","That's what I'm here for."])

def _handle_how_are_you(text, memory):
    if not _contains(text,"how are you","how are u","you okay","you good",
                     "are you okay","how do you feel","how's aria"):
        return None
    return random.choice([
        "All systems nominal. Running optimally.",
        "Fully operational, thank you for asking.",
        "I don't experience fatigue, so — excellent.",
        "Systems green. Ready for your next command.",
    ])

def _handle_wikipedia(text, memory):
    for p in [r"(?:wikipedia|wiki)\s+(?:search\s+)?(?:for\s+)?(.+)",
              r"(?:look up|lookup|search wikipedia for)\s+(.+)",
              r"(?:tell me about|what is|who is|who was|what was|what are|who are)\s+(.+?)\s+(?:on wikipedia|from wikipedia|according to wikipedia)"]:
        q = _extract(text, p)
        if q and len(q) > 2:
            return wikipedia_search_and_summarize(q)
    if text.startswith("wiki "):
        q = text[5:].strip()
        if q:
            return wikipedia_search_and_summarize(q)
    return None

def _handle_web_search(text, memory):
    for p in [r"(?:search|search for|google|look up|find|search the web for)\s+(.+)",
              r"(?:search online for|search duckduckgo for|web search)\s+(.+)"]:
        q = _extract(text, p)
        if q and len(q) > 2:
            return web_search(q)
    return None

def _handle_question_fallback(text, memory):
    starters = [
        "what is","what are","what was","what were",
        "who is","who are","who was","who were",
        "where is","where are","where was",
        "when is","when was","when did",
        "why is","why are","why does","why did",
        "how does","how do","how did","how is",
        "tell me about","explain","define","definition of",
        "meaning of","history of","facts about",
    ]
    if not any(text.startswith(s) or f" {s} " in f" {text} " for s in starters):
        if len(text.split()) < 3 or not text.endswith("?"):
            return None
    query = re.sub(r"[?!]+$", "", text).strip()
    query = re.sub(r"^(?:please\s+|can you\s+|could you\s+)", "", query, flags=re.IGNORECASE)
    result = wikipedia_search_and_summarize(query)
    if result and "No Wikipedia article" not in result:
        return result
    return web_search(query)

# ---------------------------------------------------------------------------
# NEW: Weather
# ---------------------------------------------------------------------------

def _handle_weather(text, memory):
    weather_triggers = [
        "weather","temperature","forecast","raining","snowing",
        "sunny","cloudy","humidity","wind speed","how hot","how cold",
        "what's the weather","whats the weather","is it raining",
        "will it rain","should i bring","umbrella",
    ]
    if not _contains(text, *weather_triggers):
        return None

    # Extract location: "weather in X", "weather for X", "X weather"
    location = (
        _extract(text, r"weather\s+(?:in|for|at)\s+(.+?)(?:\?|$)") or
        _extract(text, r"(?:in|for|at)\s+(.+?)\s+weather") or
        _extract(text, r"weather\s+(?:in|for|at)?\s*(.+?)(?:\s*\?|$)") or
        ""
    )

    # Strip common filler words that aren't part of the location
    if location:
        location = re.sub(
            r"^\s*(?:the\s+)?(?:city\s+of\s+)?", "", location, flags=re.IGNORECASE
        ).strip()
        location = re.sub(r"\s*(?:today|tomorrow|now|currently|right now)\s*$",
                          "", location, flags=re.IGNORECASE).strip()

    return get_weather(location)

# ---------------------------------------------------------------------------
# NEW: Unit & currency conversion
# ---------------------------------------------------------------------------

def _handle_conversion(text, memory):
    conversion_triggers = [
        "convert","to usd","to eur","to gbp","to jpy","to btc",
        " to km"," to miles"," to kg"," to lbs"," to celsius",
        " to fahrenheit"," to meters"," to feet"," to liters",
        "how many","how much is","exchange rate",
    ]
    if not _contains(text, *conversion_triggers):
        return None

    result = convert(text)
    return result   # None means "not a conversion" → fall through to LLM

# ---------------------------------------------------------------------------
# NEW: Clipboard
# ---------------------------------------------------------------------------

def _handle_clipboard(text, memory):
    # Read
    if _contains(text, "read my clipboard","what's in my clipboard",
                 "whats in my clipboard","clipboard content","show clipboard",
                 "paste from clipboard","read clipboard"):
        return handle_clipboard_read()

    # Write / copy
    copy_match = _extract(text, r"(?:copy|write to clipboard|add to clipboard)\s+['\"]?(.+?)['\"]?\s*(?:to clipboard)?$")
    if copy_match and _contains(text, "copy","clipboard"):
        return handle_clipboard_write(copy_match)

    return None

# ---------------------------------------------------------------------------
# NEW: Screenshot
# ---------------------------------------------------------------------------

def _handle_screenshot(text, memory):
    if not _contains(text, "screenshot","screen capture","capture my screen",
                     "take a screenshot","what's on my screen","whats on my screen",
                     "capture screen","save screenshot"):
        return None

    import os
    vision_model = os.getenv("OLLAMA_VISION_MODEL", "").strip() or None
    ollama_host  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    return handle_screenshot(ollama_host=ollama_host, vision_model=vision_model)

# ---------------------------------------------------------------------------
# NEW: Reminders / scheduler
# ---------------------------------------------------------------------------

def _handle_reminders(text, memory):
    # List reminders
    if _contains(text, "show reminders","list reminders","my reminders",
                 "pending reminders","what reminders","any reminders"):
        reminders = scheduler_engine.list_reminders()
        if not reminders:
            return "No pending reminders."
        import datetime
        lines = ["**Pending reminders:**"]
        for r in reminders:
            fire_dt = datetime.datetime.fromtimestamp(r["fire_at"])
            time_str = fire_dt.strftime("%H:%M on %b %d")
            lines.append(f"- `{r['id']}` — *{r['text']}* at **{time_str}**")
        return "\n".join(lines)

    # Cancel all
    if _contains(text, "cancel all reminders","clear all reminders","delete all reminders"):
        n = scheduler_engine.cancel_all()
        return f"Cancelled **{n}** reminder{'s' if n != 1 else ''}."

    # Cancel by id
    cancel_id = _extract(text, r"(?:cancel|delete|remove)\s+reminder\s+([a-f0-9]{6,8})")
    if cancel_id:
        if scheduler_engine.cancel_reminder(cancel_id):
            return f"Reminder `{cancel_id}` cancelled."
        return f"No reminder found with id `{cancel_id}`."

    # Add reminder — must match before generic "remind" check
    if not scheduler_engine.is_available():
        if _contains(text, "remind me","set a reminder","set reminder"):
            return (
                "Reminders require **apscheduler**.\n"
                "Install it with: `pip install apscheduler` and restart ARIA."
            )
        return None

    if _contains(text, "remind me","set a reminder","set reminder","reminder for"):
        parsed = scheduler_engine.parse_reminder_text(text)
        if not parsed:
            return (
                "I couldn't parse that reminder. Try:\n"
                "- *\"Remind me in 20 minutes to check the oven\"*\n"
                "- *\"Set a reminder for 1 hour — call mom\"*"
            )
        reminder = scheduler_engine.add_reminder(
            session_id=memory.recall("_session_id"),
            text=parsed["message"],
            in_seconds=parsed["in_seconds"],
        )
        if "error" in reminder:
            return reminder["error"]

        # Format friendly time
        secs = int(parsed["in_seconds"])
        if secs < 60:
            time_str = f"{secs} second{'s' if secs != 1 else ''}"
        elif secs < 3600:
            mins = secs // 60
            time_str = f"{mins} minute{'s' if mins != 1 else ''}"
        else:
            hrs = secs / 3600
            time_str = f"{hrs:g} hour{'s' if hrs != 1 else ''}"

        return (
            f"⏰ Reminder set! I'll remind you to **{parsed['message']}** "
            f"in **{time_str}**. *(id: `{reminder['id']}`)*"
        )

    return None

# ---------------------------------------------------------------------------
# NEW: Conversation history search
# ---------------------------------------------------------------------------

def _handle_history_search(text, memory):
    if not _contains(text, "search history","search my history","find in history",
                     "history search","past conversations","previous conversations",
                     "chat history","conversation history","how many messages"):
        return None

    # Stats
    if _contains(text, "how many messages","total messages","conversation stats"):
        try:
            from sysinfo.conversations import store
            stats = store.stats()
            total_s = stats.get("total_sessions", 0)
            total_m = stats.get("total_messages", 0)
            return (
                f"**Conversation history:**\n"
                f"- **{total_s}** sessions\n"
                f"- **{total_m}** total messages"
            )
        except Exception:
            return "Conversation history database not available."

    # Search
    query = (
        _extract(text, r"search(?:\s+(?:history|my history|conversations?))?\s+for\s+(.+)") or
        _extract(text, r"find\s+(?:in\s+history\s+)?['\"]?(.+?)['\"]?\s*(?:in\s+history)?$") or
        _extract(text, r"history\s+search\s+(.+)")
    )
    if query:
        try:
            from sysinfo.conversations import store
            results = store.search(query, limit=5)
            if not results:
                return f"No messages found matching **\"{query}\"** in history."
            import datetime
            lines = [f"**History results for \"{query}\":**"]
            for r in results:
                dt = datetime.datetime.fromtimestamp(r["ts"]).strftime("%b %d %H:%M")
                role_label = "You" if r["role"] == "user" else "ARIA"
                preview = r["text"][:120] + ("..." if len(r["text"]) > 120 else "")
                lines.append(f"- [{dt}] **{role_label}:** *{preview}*")
            return "\n".join(lines)
        except Exception as e:
            return f"History search failed: {e}"

    return None

# ── Music ────────────────────────────────────────────────────────────────────

def _handle_music(text, memory):
    stop_phrases = [
        "stop music","pause music","stop the music","pause the music",
        "stop playing","pause playing","stop song","turn off music",
        "mute music","stop playback","end music",
    ]
    if text.strip() in ("stop", "pause"):
        if music_engine.current_track():
            music_engine.stop()
            return MUSIC_STOP_PREFIX
        return None
    if any(text == p or text.startswith(p) for p in stop_phrases):
        music_engine.stop()
        return MUSIC_STOP_PREFIX

    if _contains(text,"what's playing","whats playing","now playing",
                 "current song","current track","what song","what music"):
        track = music_engine.current_track()
        if track:
            mins, secs = divmod(track["duration"], 60)
            dur = f"{mins}:{secs:02d}" if mins else f"{secs}s"
            return f"Now playing: **{track['title']}** ({dur})"
        return "Nothing is playing right now. Say **'play [song name]'** to start."

    for pattern in [
        r"^play\s+(.+)",
        r"^put on\s+(.+)",
        r"^start playing\s+(.+)",
        r"^i want to (?:listen|hear)\s+(?:to\s+)?(.+)",
        r"^can you play\s+(.+)",
        r"^please play\s+(.+)",
    ]:
        m = re.match(pattern, text, re.IGNORECASE)
        if m:
            query = m.group(1).strip()
            query = re.sub(r"^(?:some|a bit of|me some|me )\s+", "", query, flags=re.IGNORECASE)
            if not music_engine.is_available():
                return ("Music playback requires **yt-dlp**.\n"
                        "Install it with: `pip install yt-dlp`\nThen restart ARIA.")
            return f"{MUSIC_PLAY_PREFIX}{query}"

    return None

# ── Ollama ───────────────────────────────────────────────────────────────────

def _handle_force_ollama(text, memory):
    for prefix in ("use ollama ","ask llm ","ask the llm ","llm "):
        if text.startswith(prefix):
            real = text[len(prefix):].strip()
            if not real:
                return "What would you like me to ask the LLM?"
            return _call_ollama(real, memory)
    return None

def _call_ollama(user_input, memory):
    if not ollama_engine.is_available():
        return None
    history = memory.recent(10)
    try:
        response = ollama_engine.generate(user_input, history)
        logger.info(f"[LLM] {ollama_engine.active_model()} responded ({len(response)} chars)")
        return response
    except TimeoutError:
        return (f"**{ollama_engine.active_model()}** is taking too long to respond. "
                "Try again in a moment.")
    except RuntimeError as exc:
        logger.warning(f"[LLM] Ollama error: {exc}")
        return None

def _handle_ollama_status(text, memory):
    if not _contains(text,"ollama status","ollama info","llm status",
                     "which model","what model","what llm"):
        return None
    if not ollama_engine.is_available():
        return ("**Ollama** is not running.\n"
                "Install it at https://ollama.com/download, then run:\n"
                "`ollama pull mistral` and restart ARIA.")
    models = ollama_engine.list_models()
    active = ollama_engine.active_model()
    model_list = "\n".join(f"  - {m}" for m in models) if models else "  (none pulled yet)"
    return (f"**Ollama** is online.\n**Active model:** `{active}`\n"
            f"**Pulled models:**\n{model_list}\n\n"
            "Set a different model with the `OLLAMA_MODEL` env var and restart ARIA.")

def _handle_llm_fallback(text, memory):
    return _call_ollama(text, memory)

def _handle_unknown(text, memory):
    return ("I'm not sure how to handle that. You can:\n"
            "- Ask me anything (I'll search the web)\n"
            "- Say **'search [topic]'** for a web search\n"
            "- Say **'wiki [topic]'** for a Wikipedia summary\n"
            "- Say **'play [song]'** to play music\n"
            "- Say **'weather in [city]'** for the forecast\n"
            "- Say **'convert [x] to [y]'** for unit/currency conversion\n"
            "- Say **'help'** to see all my built-in commands")

# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS = [
    _handle_greeting,
    _handle_identity,
    _handle_name_learning,
    _handle_name_recall,
    _handle_how_are_you,
    _handle_farewell,
    _handle_thanks,
    _handle_help,
    _handle_memory_commands,
    _handle_history,
    _handle_history_search,       # NEW
    _handle_date,
    _handle_time,
    _handle_weather,              # NEW — before calculation (avoids "how hot" clash)
    _handle_conversion,           # NEW — before calculation
    _handle_calculation,
    _handle_reminders,            # NEW
    _handle_clipboard,            # NEW
    _handle_screenshot,           # NEW
    _handle_music,
    _handle_files,
    _handle_open_app,
    _handle_system,
    _handle_wikipedia,
    _handle_web_search,
    _handle_question_fallback,
    _handle_ollama_status,
    _handle_force_ollama,
    _handle_llm_fallback,
]

_LLM_HANDLERS = {_handle_force_ollama, _handle_llm_fallback}


def process(user_input: str, memory: Memory) -> str:
    text = user_input.lower().strip()
    memory.add("user", user_input)
    for handler in HANDLERS:
        response = handler(text, memory)
        if response is not None:
            memory.add("aria", response)
            return response
    response = _handle_unknown(text, memory)
    memory.add("aria", response)
    return response


async def process_stream(user_input: str, memory: Memory) -> AsyncIterator[str]:
    text = user_input.lower().strip()
    memory.add("user", user_input)

    for handler in HANDLERS:
        if handler in _LLM_HANDLERS:
            continue
        response = handler(text, memory)
        if response is not None:
            memory.add("aria", response)
            yield response
            return

    # LLM streaming
    llm_input = text
    for prefix in ("use ollama ","ask llm ","ask the llm ","llm "):
        if text.startswith(prefix):
            stripped = text[len(prefix):].strip()
            if not stripped:
                yield "What would you like me to ask the LLM?"
                return
            llm_input = stripped
            break

    if ollama_engine.is_available():
        history = memory.recent(10)
        full_parts: list[str] = []
        had_chunks = False
        try:
            async for chunk in ollama_engine.generate_stream_async(llm_input, history):
                full_parts.append(chunk)
                had_chunks = True
                yield chunk
        except TimeoutError:
            msg = (f"**{ollama_engine.active_model()}** is taking too long. Try again.")
            memory.add("aria", msg)
            yield msg
            return
        except RuntimeError as exc:
            logger.warning(f"[LLM stream] {exc}")
            had_chunks = False
        if had_chunks:
            memory.add("aria", "".join(full_parts))
            return

    response = _handle_unknown(text, memory)
    memory.add("aria", response)
    yield response