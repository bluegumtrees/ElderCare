"""SQLite 数据层：消息、会话、检索快照、记忆、迁移。"""
import json

from app.db import (
    get_conn,
    get_recent_messages,
    get_session_messages,
    get_warn_messages_since_hours,
    init_db,
    list_conversations,
    list_facts,
    replace_facts,
    save_message,
    touch_conversation,
)


def test_message_roundtrip_with_refs(tmp_db):
    refs = [{"text": "参考片段", "metadata": {"source": "test"}, "rerank_score": 0.9}]
    save_message("s1", "user", "血压高怎么办", intent="HEALTH", risk_level="low", log_level="INFO")
    save_message(
        "s1", "assistant", "注意清淡 [1]",
        intent="HEALTH", risk_level="low", log_level="INFO",
        refs=json.dumps(refs, ensure_ascii=False),
    )
    msgs = get_session_messages("s1")
    assert len(msgs) == 2
    assert msgs[0]["refs"] is None
    assert msgs[1]["refs"][0]["metadata"]["source"] == "test"  # JSON 已解析


def test_recent_messages_order_and_limit(tmp_db):
    for i in range(10):
        save_message("s2", "user" if i % 2 == 0 else "assistant", f"msg{i}")
    recent = get_recent_messages("s2", n_turns=2)
    assert [m["content"] for m in recent] == ["msg6", "msg7", "msg8", "msg9"]


def test_conversation_upsert_keeps_title(tmp_db):
    touch_conversation("s3", user_id=None, title_seed="第一句话作为标题")
    touch_conversation("s3", user_id=None, title_seed="第二句话不该覆盖标题")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT title FROM conversations WHERE session_id='s3'"
        ).fetchone()
    assert row["title"] == "第一句话作为标题"


def test_list_conversations_scoped_to_user(tmp_db):
    touch_conversation("mine", user_id=1, title_seed="我的")
    touch_conversation("others", user_id=2, title_seed="别人的")
    touch_conversation("anon", user_id=None, title_seed="匿名的")
    save_message("mine", "user", "hi")
    titles = [c["title"] for c in list_conversations(1)]
    assert titles == ["我的"]


def test_refs_column_migration(tmp_db, monkeypatch, tmp_path):
    """老库（无 refs 列）跑 init_db 后自动补列。"""
    from app.config import get_settings

    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "old.db"))
    get_settings.cache_clear()
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
            "role TEXT, content TEXT, intent TEXT, risk_level TEXT, "
            "log_level TEXT, created_at TEXT)"
        )
    init_db()
    save_message("m1", "assistant", "带快照", refs="[]")
    assert get_session_messages("m1")[0]["content"] == "带快照"


def test_facts_replace_and_list(tmp_db):
    replace_facts("u1", ["血压偏高", "独居"])
    replace_facts("u1", ["血压偏高", "孙子暑假回来"])  # 全量替换
    assert list_facts("u1") == ["血压偏高", "孙子暑假回来"]
    assert list_facts("u2") == []


def test_warn_messages_since(tmp_db):
    save_message("s4", "user", "普通", log_level="INFO")
    save_message("s4", "user", "有点闷", intent="PSYCH", risk_level="mid", log_level="WARN")
    save_message("s4", "user", "被骗了", intent="FRAUD", risk_level="high", log_level="ALERT")
    rows = get_warn_messages_since_hours(24)
    assert [r["log_level"] for r in rows] == ["ALERT", "WARN"]  # ALERT 排前
