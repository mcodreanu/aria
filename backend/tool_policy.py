"""Tool risk classification and lightweight confirmation policy."""

from __future__ import annotations

from dataclasses import dataclass


SAFE = "safe"
READ_LOCAL = "read-local"
WRITE_LOCAL = "write-local"
EXTERNAL = "external"
SYSTEM = "system"


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    level: str
    confirmation_required: bool = False


POLICIES: dict[str, ToolPolicy] = {
    "time": ToolPolicy("time", SAFE),
    "date": ToolPolicy("date", SAFE),
    "calculator": ToolPolicy("calculator", SAFE),
    "weather": ToolPolicy("weather", EXTERNAL),
    "search": ToolPolicy("search", EXTERNAL),
    "ollama": ToolPolicy("ollama", EXTERNAL),
    "music": ToolPolicy("music", EXTERNAL),
    "files.read": ToolPolicy("files.read", READ_LOCAL),
    "files.write": ToolPolicy("files.write", WRITE_LOCAL, True),
    "files.delete": ToolPolicy("files.delete", WRITE_LOCAL, True),
    "apps.open": ToolPolicy("apps.open", SYSTEM, True),
    "clipboard.read": ToolPolicy("clipboard.read", SYSTEM, True),
    "clipboard.write": ToolPolicy("clipboard.write", SYSTEM, True),
    "screenshot": ToolPolicy("screenshot", SYSTEM, True),
}


def requires_confirmation(tool_name: str) -> bool:
    return POLICIES.get(tool_name, ToolPolicy(tool_name, SYSTEM, True)).confirmation_required


def confirmation_message(tool_name: str) -> str:
    policy = POLICIES.get(tool_name)
    level = policy.level if policy else SYSTEM
    return f"ARIA needs confirmation to run `{tool_name}` ({level})."
