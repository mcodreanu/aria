"""Typed persistent memory records for ARIA."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from memory import DATA_DIR, MEMORY_FILE

MEMORY_TYPES = {"identity", "preference", "project", "deadline", "note"}
TYPED_MEMORY_FILE = DATA_DIR / "aria_memory_typed.json"


def _now() -> float:
    return time.time()


def _read_json(path: Path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if data is not None else default
    except Exception:
        pass
    return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _record(mem_type: str, key: str, value: str, source: str = "user", confidence: float = 1.0) -> dict:
    ts = _now()
    return {
        "id": str(uuid.uuid4())[:8],
        "type": mem_type if mem_type in MEMORY_TYPES else "note",
        "key": key.strip() or "note",
        "value": value.strip(),
        "source": source,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "created_at": ts,
        "updated_at": ts,
    }


def _migrate_flat(records: list[dict]) -> list[dict]:
    if records:
        return records
    flat = _read_json(MEMORY_FILE, {})
    if not isinstance(flat, dict):
        return []
    migrated: list[dict] = []
    for key, value in flat.items():
        if str(key).startswith("_"):
            continue
        mem_type = "identity" if key == "user_name" else "note"
        migrated.append(_record(mem_type, str(key), str(value), source="legacy", confidence=0.9))
    if migrated:
        _write_json(TYPED_MEMORY_FILE, migrated)
    return migrated


def list_records(mem_type: str | None = None) -> list[dict]:
    records = _migrate_flat(_read_json(TYPED_MEMORY_FILE, []))
    if mem_type:
        records = [r for r in records if r.get("type") == mem_type]
    return sorted(records, key=lambda r: r.get("updated_at", 0), reverse=True)


def add_record(mem_type: str, key: str, value: str, source: str = "user", confidence: float = 1.0) -> dict:
    records = list_records()
    rec = _record(mem_type, key, value, source, confidence)
    records.append(rec)
    _write_json(TYPED_MEMORY_FILE, records)
    return rec


def delete_record(record_id: str) -> bool:
    records = list_records()
    kept = [r for r in records if r.get("id") != record_id]
    if len(kept) == len(records):
        return False
    _write_json(TYPED_MEMORY_FILE, kept)
    return True


def forget_by_query(mem_type: str | None, query: str) -> int:
    q = query.lower().strip()
    records = list_records()
    kept = []
    removed = 0
    for rec in records:
        hay = f"{rec.get('key', '')} {rec.get('value', '')}".lower()
        if (not mem_type or rec.get("type") == mem_type) and q and q in hay:
            removed += 1
        else:
            kept.append(rec)
    if removed:
        _write_json(TYPED_MEMORY_FILE, kept)
    return removed


def relevant(query: str, limit: int = 8) -> list[dict]:
    words = {w for w in query.lower().split() if len(w) > 2}
    scored = []
    for rec in list_records():
        text = f"{rec.get('key', '')} {rec.get('value', '')}".lower()
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((score, rec))
    scored.sort(key=lambda item: (item[0], item[1].get("updated_at", 0)), reverse=True)
    return [rec for _score, rec in scored[:limit]]
