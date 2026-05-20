"""
conversations.py — Persistent conversation history via SQLite.

Every message (user + ARIA) is stored in conversations.db next to this file.
The LLM receives the last N messages from the CURRENT session for context,
plus can search all history by keyword.

Schema:
    sessions(id, started_at, label)
    messages(id, session_id, role, text, ts)

Public API:
    ConversationStore  — main class, one instance shared across the app
    .new_session()     -> session_id: int
    .add(session_id, role, text)
    .recent(session_id, n) -> list[dict]   — for LLM context
    .search(query, limit)  -> list[dict]   — full-text across all history
    .sessions(limit)       -> list[dict]   — recent sessions for /history UI
    .session_messages(session_id) -> list[dict]
    .delete_session(session_id)
"""

import sqlite3
import time
import os
import threading
from typing import Optional
from memory import DATA_DIR

DB_PATH = DATA_DIR / "conversations.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL    NOT NULL,
    label      TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL,   -- 'user' | 'aria'
    text       TEXT    NOT NULL,
    ts         REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts      ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_role_ts ON messages(role, ts);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    summary    TEXT    NOT NULL DEFAULT '',
    updated_at REAL    NOT NULL
);

-- Full-text search virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(text, content='messages', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;
"""


class ConversationStore:
    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        """Per-thread connection (SQLite connections aren't thread-safe)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    # ── Sessions ──────────────────────────────────────────────────────────────

    def new_session(self) -> int:
        """Create a new session and return its id."""
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO sessions(started_at, label) VALUES (?, ?)",
            (time.time(), "")
        )
        conn.commit()
        return cur.lastrowid

    def set_session_label(self, session_id: int, label: str):
        conn = self._conn()
        conn.execute("UPDATE sessions SET label=? WHERE id=?", (label[:120], session_id))
        conn.commit()

    def sessions(self, limit: int = 50) -> list[dict]:
        """Return recent sessions with their first user message as preview."""
        conn = self._conn()
        rows = conn.execute("""
            SELECT s.id, s.started_at, s.label,
                   (SELECT text FROM messages
                    WHERE session_id=s.id AND role='user'
                    ORDER BY ts LIMIT 1) AS preview,
                   (SELECT COUNT(*) FROM messages WHERE session_id=s.id) AS msg_count
            FROM sessions s
            ORDER BY s.started_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: int):
        conn = self._conn()
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        conn.commit()

    # ── Messages ──────────────────────────────────────────────────────────────

    def add(self, session_id: int, role: str, text: str):
        """Persist a single message."""
        if not text or not text.strip():
            return
        conn = self._conn()
        conn.execute(
            "INSERT INTO messages(session_id, role, text, ts) VALUES (?,?,?,?)",
            (session_id, role, text.strip(), time.time())
        )
        conn.commit()

        # Auto-label the session from the first user message
        row = conn.execute(
            "SELECT label FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row and not row["label"] and role == "user":
            label = text.strip()[:80]
            self.set_session_label(session_id, label)

    def recent(self, session_id: int, n: int = 20) -> list[dict]:
        """Return the last n messages for LLM context."""
        conn = self._conn()
        rows = conn.execute("""
            SELECT role, text, ts FROM messages
            WHERE session_id=?
            ORDER BY ts DESC LIMIT ?
        """, (session_id, n)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def session_messages(self, session_id: int, limit: int = 200, offset: int = 0) -> list[dict]:
        """Return ALL messages in a session (for the history UI)."""
        conn = self._conn()
        rows = conn.execute("""
            SELECT id, role, text, ts FROM messages
            WHERE session_id=? ORDER BY ts LIMIT ? OFFSET ?
        """, (session_id, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def summary(self, session_id: int) -> str:
        conn = self._conn()
        row = conn.execute(
            "SELECT summary FROM session_summaries WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return row["summary"] if row else ""

    def upsert_summary(self, session_id: int, summary: str):
        conn = self._conn()
        conn.execute("""
            INSERT INTO session_summaries(session_id, summary, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                summary=excluded.summary,
                updated_at=excluded.updated_at
        """, (session_id, summary.strip()[:4000], time.time()))
        conn.commit()

    def maybe_refresh_summary(self, session_id: int, every_messages: int = 12):
        """Create a compact extractive summary without invoking the LLM."""
        conn = self._conn()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id=?",
            (session_id,),
        ).fetchone()["n"]
        if count < every_messages or count % every_messages != 0:
            return
        rows = conn.execute("""
            SELECT role, text FROM messages
            WHERE session_id=? ORDER BY ts DESC LIMIT ?
        """, (session_id, every_messages)).fetchall()
        snippets = []
        for r in reversed(rows):
            text = " ".join(r["text"].split())
            if len(text) > 220:
                text = text[:220] + "..."
            snippets.append(f"{r['role']}: {text}")
        prior = self.summary(session_id)
        summary = "\n".join([prior, *snippets]).strip()
        self.upsert_summary(session_id, summary[-4000:])

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across all messages. Returns matches with session info."""
        if not query.strip():
            return []
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT m.id, m.session_id, m.role, m.text, m.ts,
                       s.started_at AS session_started,
                       s.label      AS session_label
                FROM messages_fts f
                JOIN messages m ON m.id = f.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ?
                ORDER BY m.ts DESC
                LIMIT ?
            """, (query, limit)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            # FTS query syntax error — fall back to LIKE
            rows = conn.execute("""
                SELECT m.id, m.session_id, m.role, m.text, m.ts,
                       s.started_at AS session_started,
                       s.label      AS session_label
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.text LIKE ?
                ORDER BY m.ts DESC
                LIMIT ?
            """, (f"%{query}%", limit)).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        conn = self._conn()
        row = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM sessions)  AS total_sessions,
                (SELECT COUNT(*) FROM messages)  AS total_messages,
                (SELECT MIN(ts)  FROM messages)  AS oldest_ts
        """).fetchone()
        return dict(row) if row else {}


# Module-level singleton
store = ConversationStore()
