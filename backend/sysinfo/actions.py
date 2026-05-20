"""Pending action inbox and approval executor."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from memory import DATA_DIR

import tools
from sysinfo.clipboard_tool import handle_clipboard_read, handle_clipboard_write, handle_screenshot

ACTIONS_FILE = DATA_DIR / "actions.json"
TRUST_FILE = DATA_DIR / "action_trust.json"
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


def _now() -> float:
    return time.time()


def _load() -> list[dict]:
    try:
        if ACTIONS_FILE.exists():
            data = json.loads(ACTIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save(actions: list[dict]) -> None:
    tmp = ACTIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(actions, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ACTIONS_FILE)


def _load_trust() -> list[dict]:
    try:
        if TRUST_FILE.exists():
            data = json.loads(TRUST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_trust(rules: list[dict]) -> None:
    tmp = TRUST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, TRUST_FILE)


def _trust_scope(payload: dict) -> str:
    tool = payload.get("tool", "")
    args = payload.get("args", {})
    if tool in {"files.write", "files.delete"}:
        return str(args.get("filename", "*"))
    if tool == "apps.open":
        return str(args.get("app", "*")).lower()
    return "*"


def _trust_key(payload: dict) -> tuple[str, str]:
    return str(payload.get("tool", "")), _trust_scope(payload)


def is_trusted(payload: dict) -> bool:
    tool, scope = _trust_key(payload)
    for rule in _load_trust():
        if rule.get("tool") == tool and rule.get("scope") in {scope, "*"}:
            return True
    return False


def trust_payload(payload: dict) -> dict:
    tool, scope = _trust_key(payload)
    rules = _load_trust()
    existing = next((r for r in rules if r.get("tool") == tool and r.get("scope") == scope), None)
    if existing:
        existing["updated_at"] = _now()
        _save_trust(rules)
        return existing
    rule = {"tool": tool, "scope": scope, "created_at": _now(), "updated_at": _now()}
    rules.append(rule)
    _save_trust(rules)
    return rule


def list_trust() -> list[dict]:
    return sorted(_load_trust(), key=lambda r: r.get("updated_at", 0), reverse=True)


def revoke_trust(tool: str, scope: str = "*") -> bool:
    rules = _load_trust()
    kept = [r for r in rules if not (r.get("tool") == tool and r.get("scope") == scope)]
    if len(kept) == len(rules):
        return False
    _save_trust(kept)
    return True


def list_actions(status: str | None = None) -> list[dict]:
    actions = _load()
    if status:
        actions = [a for a in actions if a.get("status") == status]
    return sorted(actions, key=lambda a: a.get("created_at", 0), reverse=True)


def create_action(action_type: str, summary: str, payload: dict, risk: str = "medium") -> dict:
    if is_trusted(payload):
        action = {
            "id": str(uuid.uuid4())[:8],
            "type": action_type,
            "summary": summary,
            "payload": payload,
            "risk": risk,
            "status": APPROVED,
            "created_at": _now(),
            "resolved_at": _now(),
            "result": _execute(payload),
            "auto_approved": True,
        }
        actions = _load()
        actions.append(action)
        _save(actions)
        return action
    action = {
        "id": str(uuid.uuid4())[:8],
        "type": action_type,
        "summary": summary,
        "payload": payload,
        "risk": risk,
        "status": PENDING,
        "created_at": _now(),
        "resolved_at": None,
        "result": None,
        "auto_approved": False,
    }
    actions = _load()
    actions.append(action)
    _save(actions)
    return action


def get_action(action_id: str) -> dict | None:
    return next((a for a in _load() if a.get("id") == action_id), None)


def reject_action(action_id: str) -> dict | None:
    actions = _load()
    for action in actions:
        if action.get("id") == action_id:
            action["status"] = REJECTED
            action["resolved_at"] = _now()
            _save(actions)
            return action
    return None


def _execute(payload: dict) -> str:
    tool = payload.get("tool")
    args = payload.get("args", {})
    if tool == "files.write":
        return tools.create_file_tool(args.get("filename", ""), args.get("content", ""))
    if tool == "files.delete":
        return tools.delete_file_tool(args.get("filename", ""))
    if tool == "apps.open":
        return tools.open_app(args.get("app", ""))
    if tool == "clipboard.read":
        return handle_clipboard_read()
    if tool == "clipboard.write":
        return handle_clipboard_write(args.get("text", ""))
    if tool == "screenshot":
        return handle_screenshot(
            ollama_host=args.get("ollama_host", "http://localhost:11434"),
            vision_model=args.get("vision_model"),
        )
    return f"Unknown action tool: {tool}"


def approve_action(action_id: str) -> dict | None:
    actions = _load()
    for action in actions:
        if action.get("id") != action_id:
            continue
        if action.get("status") != PENDING:
            return action
        result = _execute(action.get("payload", {}))
        trust_payload(action.get("payload", {}))
        action["status"] = APPROVED
        action["resolved_at"] = _now()
        action["result"] = result
        _save(actions)
        return action
    return None
