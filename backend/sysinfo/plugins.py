"""Minimal local plugin registry for ARIA."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

from memory import DATA_DIR

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins"
PLUGIN_STATE_FILE = DATA_DIR / "plugins_state.json"
PERMISSIONS = {"read_only", "file_write", "system_action", "network"}


def _load_state() -> dict:
    try:
        if PLUGIN_STATE_FILE.exists():
            data = json.loads(PLUGIN_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    tmp = PLUGIN_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, PLUGIN_STATE_FILE)


def _manifest_paths() -> list[Path]:
    if not PLUGIN_DIR.exists():
        return []
    return sorted(PLUGIN_DIR.glob("*/plugin.json"))


def list_plugins() -> list[dict]:
    state = _load_state()
    plugins = []
    for path in _manifest_paths():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            plugin_id = manifest.get("id") or path.parent.name
            permissions = [p for p in manifest.get("permissions", []) if p in PERMISSIONS]
            plugins.append({
                "id": plugin_id,
                "name": manifest.get("name", plugin_id),
                "description": manifest.get("description", ""),
                "permissions": permissions,
                "commands": manifest.get("commands", []),
                "entrypoint": manifest.get("entrypoint", ""),
                "enabled": bool(state.get(plugin_id, manifest.get("enabled", False))),
                "path": str(path.parent),
            })
        except Exception as exc:
            plugins.append({
                "id": path.parent.name,
                "name": path.parent.name,
                "description": f"Invalid manifest: {exc}",
                "permissions": [],
                "commands": [],
                "entrypoint": "",
                "enabled": False,
                "path": str(path.parent),
            })
    return plugins


def set_enabled(plugin_id: str, enabled: bool) -> dict | None:
    plugins = list_plugins()
    if not any(p["id"] == plugin_id for p in plugins):
        return None
    state = _load_state()
    state[plugin_id] = enabled
    _save_state(state)
    return next(p for p in list_plugins() if p["id"] == plugin_id)


def handle_command(text: str, memory) -> str | None:
    for plugin in list_plugins():
        if not plugin.get("enabled"):
            continue
        commands = [str(c).lower() for c in plugin.get("commands", [])]
        if commands and not any(text.startswith(c) or c in text for c in commands):
            continue
        entrypoint = plugin.get("entrypoint", "")
        if not entrypoint or ":" not in entrypoint:
            return f"Plugin **{plugin['name']}** has no callable entrypoint."
        module_name, func_name = entrypoint.split(":", 1)
        try:
            if plugin.get("path") and plugin["path"] not in sys.path:
                sys.path.insert(0, plugin["path"])
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
            return func(text, memory)
        except Exception as exc:
            return f"Plugin **{plugin['name']}** failed: {exc}"
    return None
