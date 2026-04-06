import datetime
import math
import os
import platform
import subprocess


# ──────────────────────────────────────────────
# TIME & DATE
# ──────────────────────────────────────────────

def get_time() -> str:
    now = datetime.datetime.now()
    return f"It's {now.strftime('%H:%M')} on {now.strftime('%A, %B %d %Y')}."


def get_day() -> str:
    return datetime.datetime.now().strftime("%A")


def get_date() -> str:
    return datetime.datetime.now().strftime("%B %d, %Y")


# ──────────────────────────────────────────────
# CALCULATIONS
# ──────────────────────────────────────────────

def _safe_eval(expression: str) -> str:
    """
    FIX: Replace raw eval() with asteval (safe expression evaluator).

    asteval parses the expression into an AST before executing it, so it
    can never access builtins, import modules, or execute arbitrary code —
    even if someone injects 'os.system(...)' or '__import__(...)'.

    Falls back to the restricted-eval approach if asteval is not installed,
    so existing deployments keep working without any pip install.
    """
    try:
        from asteval import Interpreter
        aeval = Interpreter()
        result = aeval(expression)
        if aeval.error:
            # asteval accumulates error messages; surface the first one
            raise ValueError(aeval.error[0].get_error()[1])
        return result
    except ImportError:
        # Fallback: restricted eval (original behaviour).
        # Install asteval for proper sandboxing: pip install asteval
        safe_funcs = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        safe_funcs.update({"abs": abs, "round": round, "min": min,
                           "max": max, "sum": sum, "pow": pow})
        return eval(expression, {"__builtins__": {}}, safe_funcs)  # noqa: S307


def calculate(expression: str) -> str:
    """
    Evaluate a math expression and return a human-readable result string.

    FIX (greedy regex): the caller (_handle_calculation in aria_brain.py)
    was matching almost anything containing a digit.  This function now does
    a final sanity check — the expression must contain at least one operator
    or a known math function name, so plain noun phrases like "3 cats" don't
    accidentally trigger it.  The check is kept here rather than in the
    brain so the rule travels with the tool.
    """
    OPERATORS = set("+-*/%^()")
    MATH_FUNCS = {
        "sqrt", "sin", "cos", "tan", "log", "log2", "log10",
        "exp", "abs", "round", "pow", "ceil", "floor", "pi", "e",
    }

    expr = expression.replace("^", "**").replace(" x ", "*").strip()

    has_operator = any(c in expr for c in OPERATORS)
    has_func = any(fn in expr.lower() for fn in MATH_FUNCS)

    if not (has_operator or has_func):
        return (
            "I couldn't parse that as a math expression. "
            "Try something like '2 + 2', 'sqrt(16)', or '5 * 8'."
        )

    try:
        result = _safe_eval(expr)
        if isinstance(result, float) and result == int(result):
            result = int(result)
        return f"The result of {expression} is **{result}**."
    except Exception:
        return "I couldn't compute that. Try something like '2 + 2', 'sqrt(16)', or '5 * 8'."


# ──────────────────────────────────────────────
# FILE SYSTEM
# ──────────────────────────────────────────────

def list_directory(path: str = ".") -> str:
    path = os.path.expanduser(path)
    try:
        entries = os.listdir(path)
        if not entries:
            return f"The directory '{path}' is empty."
        folders = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        files   = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        result  = f"Contents of **{path}**:\n"
        if folders:
            result += "  📁 Folders: " + ", ".join(folders[:15]) + "\n"
        if files:
            result += "  📄 Files: " + ", ".join(files[:15])
        return result.strip()
    except PermissionError:
        return f"Access denied to '{path}'."
    except FileNotFoundError:
        return f"Directory '{path}' not found."


def create_file_tool(filename: str, content: str = "") -> str:
    try:
        filename = os.path.expanduser(filename)
        with open(filename, "w") as f:
            f.write(content)
        return f"File **{filename}** created successfully."
    except Exception as e:
        return f"Failed to create file: {e}"


def read_file_tool(filename: str) -> str:
    try:
        filename = os.path.expanduser(filename)
        with open(filename, "r") as f:
            content = f.read(2000)
        return f"Contents of **{filename}**:\n```\n{content}\n```"
    except FileNotFoundError:
        return f"File '{filename}' not found."
    except Exception as e:
        return f"Couldn't read file: {e}"


def delete_file_tool(filename: str) -> str:
    try:
        filename = os.path.expanduser(filename)
        os.remove(filename)
        return f"File **{filename}** deleted."
    except FileNotFoundError:
        return f"File '{filename}' not found."
    except Exception as e:
        return f"Couldn't delete file: {e}"


# ──────────────────────────────────────────────
# OPEN APPS
# ──────────────────────────────────────────────

APP_ALIASES = {
    "calculator": {"Windows": "calc",           "Darwin": "open -a Calculator",      "Linux": "gnome-calculator"},
    "notepad":    {"Windows": "notepad",         "Darwin": "open -a TextEdit",        "Linux": "gedit"},
    "browser":    {"Windows": "start chrome",    "Darwin": "open -a 'Google Chrome'", "Linux": "xdg-open https://google.com"},
    "terminal":   {"Windows": "start cmd",       "Darwin": "open -a Terminal",        "Linux": "x-terminal-emulator"},
    "explorer":   {"Windows": "explorer",        "Darwin": "open .",                  "Linux": "nautilus ."},
    "music":      {"Windows": "start wmplayer",  "Darwin": "open -a Music",           "Linux": "rhythmbox"},
    "files":      {"Windows": "explorer",        "Darwin": "open .",                  "Linux": "nautilus ."},
}

def open_app(app_name: str) -> str:
    system = platform.system()
    key = app_name.lower().strip()
    if key in APP_ALIASES:
        cmd = APP_ALIASES[key].get(system)
        if cmd:
            try:
                subprocess.Popen(cmd, shell=True)
                return f"Opening **{app_name}**..."
            except Exception as e:
                return f"Failed to open {app_name}: {e}"
        return f"'{app_name}' is not supported on {system}."
    return f"I don't know how to open '{app_name}'. I can open: {', '.join(APP_ALIASES.keys())}."


# ──────────────────────────────────────────────
# SYSTEM INFO
# ──────────────────────────────────────────────

def get_system_info() -> str:
    return (
        f"**System:** {platform.system()} {platform.release()}\n"
        f"**Machine:** {platform.machine()}\n"
        f"**Processor:** {platform.processor() or 'Unknown'}\n"
        f"**Python:** {platform.python_version()}\n"
        f"**Hostname:** {platform.node()}"
    )