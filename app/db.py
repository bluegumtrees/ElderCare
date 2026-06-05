import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings


def _connect() -> sqlite3.Connection:
    s = get_settings()
    Path(s.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(s.sqlite_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    with closing(_connect()) as conn:
        with conn:
            yield conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                risk_level TEXT,
                log_level TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);
            """
        )


def save_message(
    session_id: str,
    role: str,
    content: str,
    intent: str | None = None,
    risk_level: str | None = None,
    log_level: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, intent, risk_level, log_level) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, intent, risk_level, log_level),
        )


def get_recent_messages(session_id: str, n_turns: int = 6) -> list[dict]:
    limit = n_turns * 2
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
