"""
ARIA Brain — Rule-based NLP engine.
No external APIs, no ML models. Pure pattern matching + tools.
"""

import re
import random
from memory import Memory
from tools import (
    get_time, get_date, get_day, calculate,
    list_directory, create_file_tool, read_file_tool, delete_file_tool,
    open_app, get_system_info,
)
from search import web_search, wikipedia_search_and_summarize

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _contains(text: str, *keywords) -> bool:
    return any(k in text for k in keywords)

def _extract(text: str, pattern: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(group).strip() if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Intent handlers — each returns a string response or None (no match)
# ─────────────────────────────────────────────────────────────────────────────

def _handle_greeting(text: str, memory: Memory) -> str | None:
    greetings = ["hello", "hi", "hey", "good morning", "good evening",
                 "good afternoon", "howdy", "what's up", "sup", "greetings"]
    if not _contains(text, *greetings):
        return None
    name = memory.recall("user_name")
    responses = [
        f"Hello{', ' + name if name else ''}. ARIA online and ready.",
        f"Hey{' ' + name if name else ''}. All systems operational.",
        f"Greetings{', ' + name if name else ''}. How can I assist you?",
        "Hello. I'm here. What do you need?",
    ]
    return random.choice(responses)


def _handle_identity(text: str, memory: Memory) -> str | None:
    if not _contains(text, "who are you", "what are you", "your name",
                     "introduce yourself", "what's aria", "what is aria"):
        return None
    return (
        "I'm **ARIA** — Adaptive Reasoning & Intelligent Assistant. "
        "I run locally on your machine and can answer questions by searching "
        "DuckDuckGo and Wikipedia, manage your files, do calculations, open apps, and more. "
        "Ask me anything."
    )


def _handle_name_learning(text: str, memory: Memory) -> str | None:
    patterns = [
        r"my name is ([a-zA-Z]+)",
        r"i(?:'m| am) ([a-zA-Z]+)",
        r"call me ([a-zA-Z]+)",
        r"people call me ([a-zA-Z]+)",
    ]
    for p in patterns:
        name = _extract(text, p)
        if name and len(name) > 1 and name.lower() not in {"a", "an", "the", "aria", "fine", "good", "ok", "here"}:
            memory.remember("user_name", name.capitalize())
            return f"Got it. I'll call you **{name.capitalize()}** from now on."
    return None


def _handle_name_recall(text: str, memory: Memory) -> str | None:
    if not _contains(text, "my name", "do you know me", "what do you call me",
                     "who am i", "remember me"):
        return None
    name = memory.recall("user_name")
    if name:
        return f"Of course — you're **{name}**."
    return "I don't know your name yet. You can tell me with 'My name is ...'."


def _handle_time(text: str, memory: Memory) -> str | None:
    if not _contains(text, "time", "clock", "hour"):
        return None
    if _contains(text, "date", "day", "today"):
        return get_time()
    return get_time()


def _handle_date(text: str, memory: Memory) -> str | None:
    if _contains(text, "what day", "what's today", "today's date", "current date", "what date"):
        return f"Today is **{get_date()}** ({get_day()})."
    return None


# ── Calculation handler ───────────────────────────────────────────────────────
#
# FIX: The original fallback regex matched almost any string containing a digit
# (e.g. "I have 3 cats", "born in 1990"), causing spurious calculator calls.
#
# Two-stage fix:
#   1. Explicit trigger keywords still work as before.
#   2. The raw-expression fallback now requires at least one arithmetic
#      operator character (+  -  *  /  %  ^  **) or a known math function
#      name to be present, so plain noun phrases with digits are ignored.
#      The actual operator/function check lives in calculate() (tools.py),
#      but we gate here too to avoid even calling calculate() on clear misses.
#
_CALC_OPERATORS_RE = re.compile(r"[\+\-\*\/\%\^]|sqrt|sin|cos|tan|log|exp|pow|ceil|floor")

def _handle_calculation(text: str, memory: Memory) -> str | None:
    explicit_triggers = [
        "calculate", "compute", "what is", "what's", "how much is",
        "solve", "evaluate",
    ]

    # Path A — explicit trigger keyword present
    expr = None
    if _contains(text, *explicit_triggers):
        expr = _extract(
            text,
            r"(?:calculate|compute|what(?:'s| is)|solve|evaluate)\s+(.+)",
            1,
        )
        if not expr:
            # bare "calculate" with no follow-up
            return None

    # Path B — looks like a raw math expression (must contain an operator)
    if expr is None:
        # Only proceed if the text contains an operator or math function
        if not _CALC_OPERATORS_RE.search(text):
            return None
        candidate = _extract(
            text,
            r"([\d\s\.\+\-\*\/\%\^\(\)a-zA-Z_]+(?:\d[\d\s]*)?)",
            1,
        )
        if not candidate or not re.search(r"\d", candidate):
            return None
        expr = candidate

    # Strip anything that isn't a valid math character before passing to eval
    expr = re.sub(r"[^\d\s\.\+\-\*\/\%\^\(\)a-zA-Z_]", "", expr).strip()
    if not re.search(r"\d", expr):
        return None

    return calculate(expr)


def _handle_files(text: str, memory: Memory) -> str | None:
    if _contains(text, "list files", "show files", "list directory",
                 "what's in", "what is in", "contents of", "ls ", "dir "):
        path_match = _extract(text, r"(?:in|of|directory|folder)\s+['\"]?([^\s'\"]+)['\"]?")
        path = path_match if path_match else "."
        return list_directory(path)

    if _contains(text, "read file", "open file", "show file", "contents of file", "cat "):
        fname = _extract(text, r"(?:read|open|show|cat)\s+(?:file\s+)?['\"]?([^\s'\"]+\.\w+)['\"]?")
        if fname:
            return read_file_tool(fname)
        return "Which file should I read? Specify a filename."

    if _contains(text, "create file", "make file", "new file", "write file"):
        fname = _extract(text, r"(?:create|make|new|write)\s+(?:a\s+)?(?:file\s+)?['\"]?([^\s'\"]+\.\w+)['\"]?")
        content_match = _extract(text, r"(?:with content|containing|with text)\s+['\"]?(.+)['\"]?$")
        if fname:
            return create_file_tool(fname, content_match or "")
        return "What should I name the file?"

    if _contains(text, "delete file", "remove file", "erase file"):
        fname = _extract(text, r"(?:delete|remove|erase)\s+(?:file\s+)?['\"]?([^\s'\"]+\.\w+)['\"]?")
        if fname:
            return delete_file_tool(fname)
        return "Which file should I delete?"

    return None


def _handle_open_app(text: str, memory: Memory) -> str | None:
    if not _contains(text, "open", "launch", "start", "run"):
        return None
    app = _extract(text, r"(?:open|launch|start|run)\s+(?:the\s+)?([a-zA-Z]+)")
    if app:
        return open_app(app)
    return None


def _handle_system(text: str, memory: Memory) -> str | None:
    if _contains(text, "system info", "system information", "about this computer",
                 "my computer", "hardware", "os info", "operating system"):
        return get_system_info()
    return None


def _handle_memory_commands(text: str, memory: Memory) -> str | None:
    if _contains(text, "remember that", "don't forget", "keep in mind", "note that"):
        fact = _extract(text, r"(?:remember that|don't forget|keep in mind|note that)\s+(.+)")
        if fact:
            key = f"note_{len(memory.facts)}"
            memory.remember(key, fact)
            return f"Noted: *{fact}*"

    if _contains(text, "what do you remember", "what do you know about me",
                 "what have you noted", "show memory", "your memory"):
        if not memory.facts:
            return "I haven't noted anything about you yet this session."
        lines = "\n".join(f"- **{k}**: {v}" for k, v in memory.facts.items())
        return f"Here's what I remember:\n{lines}"

    if _contains(text, "forget everything", "clear memory", "reset memory", "forget all"):
        memory.reset()
        return "Memory cleared. Starting fresh."

    if _contains(text, "how long", "session time", "how long have we", "session duration"):
        return f"We've been talking for **{memory.session_duration()}**."

    return None


def _handle_history(text: str, memory: Memory) -> str | None:
    if not _contains(text, "last message", "what did i say", "previous message",
                     "repeat that", "what did you say"):
        return None
    last = memory.last_user_message()
    if last:
        return f"Your last message was: *\"{last}\"*"
    return "This is the beginning of our conversation."


def _handle_help(text: str, memory: Memory) -> str | None:
    if not _contains(text, "help", "what can you do", "commands", "capabilities",
                     "features", "abilities", "instructions"):
        return None
    return """Here's what I can do:

**🌐 Web Search**
- "Search for black holes" / "Search the web for Python tutorials"
- Just ask any question — I'll search automatically

**📖 Wikipedia**
- "wiki Python programming language"
- "Who is Alan Turing?" / "What is quantum computing?"
- "Tell me about the Roman Empire"

**🕐 Time & Date**
- "What time is it?" / "What's today's date?"

**🔢 Calculations**
- "Calculate 15 * 8" / "What is sqrt(144)?" / "2 ** 10"

**📁 Files**
- "List files" / "List files in ~/Documents"
- "Create file notes.txt with content Hello World"
- "Read file notes.txt" / "Delete file notes.txt"

**🖥 Apps**
- "Open calculator" / "Open browser" / "Open terminal"
- Supported: calculator, notepad, browser, terminal, explorer, music, files

**🧠 Memory**
- "My name is Alex" — I'll remember you
- "Remember that my project deadline is Friday"
- "What do you remember?" / "Clear memory"

**💻 System**
- "System info" — hardware & OS details"""


def _handle_farewell(text: str, memory: Memory) -> str | None:
    if not _contains(text, "bye", "goodbye", "see you", "exit", "quit",
                     "shut down", "goodnight", "good night", "farewell"):
        return None
    name = memory.recall("user_name")
    responses = [
        f"Goodbye{', ' + name if name else ''}. ARIA standing by.",
        f"See you{', ' + name if name else ''}. Shutting down conversational mode.",
        "Until next time. ARIA out.",
        f"Take care{', ' + name if name else ''}.",
    ]
    return random.choice(responses)


def _handle_thanks(text: str, memory: Memory) -> str | None:
    if not _contains(text, "thank", "thanks", "thx", "ty", "appreciate"):
        return None
    return random.choice([
        "You're welcome.",
        "Of course.",
        "Always.",
        "Happy to help.",
        "That's what I'm here for.",
    ])


def _handle_how_are_you(text: str, memory: Memory) -> str | None:
    if not _contains(text, "how are you", "how are u", "you okay", "you good",
                     "are you okay", "how do you feel", "how's aria"):
        return None
    return random.choice([
        "All systems nominal. Running optimally.",
        "Fully operational, thank you for asking.",
        "I don't experience fatigue, so — excellent.",
        "Systems green. Ready for your next command.",
    ])


def _handle_wikipedia(text: str, memory: Memory) -> str | None:
    """Explicit Wikipedia lookup."""
    patterns = [
        r"(?:wikipedia|wiki)\s+(?:search\s+)?(?:for\s+)?(.+)",
        r"(?:look up|lookup|search wikipedia for)\s+(.+)",
        r"(?:tell me about|what is|who is|who was|what was|what are|who are)\s+(.+?)\s+(?:on wikipedia|from wikipedia|according to wikipedia)",
    ]
    for p in patterns:
        query = _extract(text, p)
        if query and len(query) > 2:
            return wikipedia_search_and_summarize(query)

    if text.startswith("wiki "):
        query = text[5:].strip()
        if query:
            return wikipedia_search_and_summarize(query)

    return None


def _handle_web_search(text: str, memory: Memory) -> str | None:
    """Explicit web search trigger."""
    explicit_patterns = [
        r"(?:search|search for|google|look up|find|search the web for)\s+(.+)",
        r"(?:search online for|search duckduckgo for|web search)\s+(.+)",
    ]
    for p in explicit_patterns:
        query = _extract(text, p)
        if query and len(query) > 2:
            return web_search(query)
    return None


def _handle_question_fallback(text: str, memory: Memory) -> str | None:
    """
    Smart fallback: if the input looks like a factual question
    and no other handler matched, auto-search DuckDuckGo + Wikipedia.
    """
    question_starters = [
        "what is", "what are", "what was", "what were",
        "who is", "who are", "who was", "who were",
        "where is", "where are", "where was",
        "when is", "when was", "when did",
        "why is", "why are", "why does", "why did",
        "how does", "how do", "how did", "how is",
        "tell me about", "explain", "define", "definition of",
        "meaning of", "history of", "facts about",
    ]
    if not any(text.startswith(s) or f" {s} " in f" {text} " for s in question_starters):
        if len(text.split()) < 3 or text.endswith("?") is False:
            return None

    query = re.sub(r"[?!]+$", "", text).strip()
    query = re.sub(r"^(?:please\s+|can you\s+|could you\s+)", "", query, flags=re.IGNORECASE)

    result = wikipedia_search_and_summarize(query)
    if result and "No Wikipedia article" not in result:
        return result

    return web_search(query)


def _handle_unknown(text: str, memory: Memory) -> str:
    """Last resort fallback — only reached if nothing else matched."""
    return (
        "I'm not sure how to handle that. You can:\n"
        "- Ask me anything (I'll search the web)\n"
        "- Say **'search [topic]'** for a web search\n"
        "- Say **'wiki [topic]'** for a Wikipedia summary\n"
        "- Say **'help'** to see all my built-in commands"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

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
    _handle_date,
    _handle_time,
    _handle_calculation,
    _handle_files,
    _handle_open_app,
    _handle_system,
    # ── Search (explicit first, smart fallback last) ──
    _handle_wikipedia,
    _handle_web_search,
    _handle_question_fallback,
]


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