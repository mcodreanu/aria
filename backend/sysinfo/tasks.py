"""Local JSON task store for ARIA."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from memory import DATA_DIR

TASKS_FILE = DATA_DIR / "tasks.json"
STATUSES = {"todo", "active", "blocked", "done"}
PRIORITIES = {"low", "normal", "high"}


def _now() -> float:
    return time.time()


def _load() -> list[dict]:
    try:
        if TASKS_FILE.exists():
            data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save(tasks: list[dict]) -> None:
    tmp = TASKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, TASKS_FILE)


def list_tasks(status: str | None = None) -> list[dict]:
    tasks = _load()
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return sorted(tasks, key=lambda t: (t.get("status") == "done", -(t.get("updated_at", 0))))


def add_task(title: str, notes: str = "", status: str = "todo", priority: str = "normal", due_at: float | None = None) -> dict:
    now = _now()
    task = {
        "id": str(uuid.uuid4())[:8],
        "title": title.strip(),
        "notes": notes.strip(),
        "status": status if status in STATUSES else "todo",
        "priority": priority if priority in PRIORITIES else "normal",
        "due_at": due_at,
        "created_at": now,
        "updated_at": now,
    }
    tasks = _load()
    tasks.append(task)
    _save(tasks)
    return task


def update_task(task_id: str, updates: dict) -> dict | None:
    tasks = _load()
    for task in tasks:
        if task.get("id") != task_id:
            continue
        for key in ("title", "notes", "status", "priority", "due_at"):
            if key in updates:
                value = updates[key]
                if key == "status" and value not in STATUSES:
                    continue
                if key == "priority" and value not in PRIORITIES:
                    continue
                task[key] = value
        task["updated_at"] = _now()
        _save(tasks)
        return task
    return None


def delete_task(task_id: str) -> bool:
    tasks = _load()
    kept = [t for t in tasks if t.get("id") != task_id]
    if len(kept) == len(tasks):
        return False
    _save(kept)
    return True


def find_task(query: str) -> dict | None:
    q = query.lower().strip()
    if not q:
        return None
    for task in list_tasks():
        if q in task.get("id", "").lower() or q in task.get("title", "").lower():
            return task
    return None
