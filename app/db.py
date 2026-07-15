import json
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
                refs TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS auth_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                title TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_user
                ON conversations(user_id, updated_at);
            """
        )
        # 迁移：老库的 messages 表补 refs 列（存检索快照 JSON，历史回看还原引用）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
        if "refs" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN refs TEXT")


# ============ 消息 ============

def save_message(
    session_id: str,
    role: str,
    content: str,
    intent: str | None = None,
    risk_level: str | None = None,
    log_level: str | None = None,
    refs: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, intent, risk_level, log_level, refs) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, intent, risk_level, log_level, refs),
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


def get_session_messages(session_id: str, limit: int = 200) -> list[dict]:
    """整段会话的完整消息（带意图/风险标注 + 检索快照），历史回看用。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, intent, risk_level, log_level, refs, created_at "
            "FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        m = dict(r)
        if m.get("refs"):
            try:
                m["refs"] = json.loads(m["refs"])
            except (json.JSONDecodeError, TypeError):
                m["refs"] = None
        out.append(m)
    return out


# ============ 用户 ============

def create_user(username: str, password_hash: str, display_name: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, display_name) VALUES (?, ?, ?)",
            (username, password_hash, display_name or username),
        )
        return int(cur.lastrowid)


def get_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


# ============ 登录 token（服务端会话，可撤销） ============

def create_auth_token(token: str, user_id: int, ttl_days: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auth_tokens (token, user_id, expires_at) "
            "VALUES (?, ?, datetime('now', ?))",
            (token, user_id, f"+{ttl_days} days"),
        )


def get_user_by_token(token: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, u.display_name FROM auth_tokens t "
            "JOIN users u ON u.id = t.user_id "
            "WHERE t.token = ? AND t.expires_at > datetime('now')",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def delete_auth_token(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))


# ============ 会话（conversation） ============

def touch_conversation(session_id: str, user_id: int | None, title_seed: str) -> None:
    """用户发消息时调用：不存在则建档（标题取首条消息），存在则刷新时间。

    匿名会话 user_id 为 NULL；之后同一浏览器登录不回溯认领（保持简单）。
    """
    title = title_seed.strip().replace("\n", " ")[:24] or "新对话"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (session_id, user_id, title) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET updated_at = datetime('now')",
            (session_id, user_id, title),
        )


def list_conversations(user_id: int, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.session_id, c.title, c.created_at, c.updated_at, "
            "       COUNT(m.id) AS message_count "
            "FROM conversations c LEFT JOIN messages m ON m.session_id = c.session_id "
            "WHERE c.user_id = ? "
            "GROUP BY c.session_id ORDER BY c.updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(session_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None
