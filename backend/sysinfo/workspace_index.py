"""Local workspace file index and search."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from memory import DATA_DIR
from settings import WORKSPACE_ROOT

INDEX_FILE = DATA_DIR / "workspace_index.json"
MAX_SNIPPET_CHARS = 4000
TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".html", ".css", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".csv", ".tsv", ".xml", ".rst", ".log",
}


def _write(data: dict) -> None:
    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, INDEX_FILE)


def _read() -> dict:
    try:
        if INDEX_FILE.exists():
            data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"indexed_at": None, "root": str(WORKSPACE_ROOT), "files": []}


def _safe_files() -> list[Path]:
    root = WORKSPACE_ROOT.resolve()
    if not root.exists():
        return []
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
            if resolved == root or root in resolved.parents:
                files.append(resolved)
        except Exception:
            continue
    return files


def build_index() -> dict:
    root = WORKSPACE_ROOT.resolve()
    entries = []
    for path in _safe_files():
        ext = path.suffix.lower()
        snippet = ""
        if ext in TEXT_EXTS:
            try:
                snippet = path.read_text(encoding="utf-8", errors="replace")[:MAX_SNIPPET_CHARS]
            except Exception:
                snippet = ""
        stat = path.stat()
        entries.append({
            "path": str(path.relative_to(root)),
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "extension": ext,
            "snippet": snippet,
        })
    data = {"indexed_at": time.time(), "root": str(root), "files": entries}
    _write(data)
    return data


def files() -> list[dict]:
    return _read().get("files", [])


def search(query: str, limit: int = 20) -> list[dict]:
    q = query.lower().strip()
    if not q:
        return []
    if not INDEX_FILE.exists():
        build_index()
    matches = []
    for item in files():
        hay = f"{item.get('path', '')}\n{item.get('snippet', '')}".lower()
        if q in hay:
            snippet = item.get("snippet", "")
            pos = snippet.lower().find(q)
            if pos >= 0:
                start = max(0, pos - 120)
                end = min(len(snippet), pos + len(q) + 180)
                preview = snippet[start:end].strip()
            else:
                preview = snippet[:240].strip()
            out = dict(item)
            out["preview"] = preview
            matches.append(out)
    return matches[:limit]


def summarize_folder(path: str = ".") -> str:
    root = WORKSPACE_ROOT.resolve()
    folder = (root / path).resolve()
    if folder != root and root not in folder.parents:
        return "That folder is outside ARIA's workspace."
    if not folder.exists() or not folder.is_dir():
        return "Folder not found."
    items = list(folder.iterdir())
    dirs = [p for p in items if p.is_dir()]
    fs = [p for p in items if p.is_file()]
    return (
        f"**Workspace folder `{folder.relative_to(root) if folder != root else '.'}`**\n"
        f"- {len(dirs)} folders\n"
        f"- {len(fs)} files\n"
        f"- Common extensions: {', '.join(sorted({p.suffix or '(none)' for p in fs})[:8]) or 'none'}"
    )
