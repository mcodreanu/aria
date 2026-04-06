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

def calculate(expression: str) -> str:
    safe_funcs = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    safe_funcs.update({"abs": abs, "round": round, "min": min, "max": max, "sum": sum, "pow": pow})
    try:
        expression = expression.replace("^", "**").replace(" x ", "*").strip()
        result = eval(expression, {"__builtins__": {}}, safe_funcs)
        if isinstance(result, float) and result.is_integer():
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