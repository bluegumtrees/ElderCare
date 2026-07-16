"""意图分类器：解析兜底、日志级别映射、历史拼接、LLM 失败降级。"""
import asyncio

import app.intent as intent_mod
from app.intent import _format_input, _safe_parse, classify_intent, derive_log_level


def test_safe_parse_plain_json():
    assert _safe_parse('{"intent":"CHAT"}') == {"intent": "CHAT"}


def test_safe_parse_extracts_embedded_block():
    raw = '好的，结果是：{"intent":"HEALTH","risk_level":"low"} 以上'
    assert _safe_parse(raw)["intent"] == "HEALTH"


def test_safe_parse_garbage_returns_empty():
    assert _safe_parse("完全不是 JSON") == {}


def test_derive_log_level_matrix():
    assert derive_log_level("EMERGENCY", "high") == "ALERT"
    assert derive_log_level("FRAUD", "high") == "ALERT"
    assert derive_log_level("PSYCH", "low") == "INFO"
    assert derive_log_level("PSYCH", "mid") == "WARN"
    assert derive_log_level("PSYCH", "high") == "ALERT"
    assert derive_log_level("CHAT", "low") == "INFO"
    assert derive_log_level("HEALTH", "mid") == "INFO"


def test_format_input_without_history():
    out = _format_input("你好", None)
    assert out == "用户最新的话：你好"


def test_format_input_with_history_truncates_and_labels():
    history = [
        {"role": "user", "content": "我血压有点高" * 50},
        {"role": "assistant", "content": "注意清淡饮食"},
    ]
    out = _format_input("那吃什么好？", history)
    assert "老人：" in out and "助手：" in out
    assert "用户最新的话：那吃什么好？" in out
    # 历史单条截断到 100 字
    first_line = out.split("\n")[1]
    assert len(first_line) <= 110


def test_classify_intent_falls_back_on_llm_error(monkeypatch):
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise RuntimeError("network down")

    monkeypatch.setattr(intent_mod, "get_llm", lambda: _Boom())
    result = asyncio.run(classify_intent("随便说点什么"))
    assert result == {"intent": "CHAT", "risk_level": "low", "query": "随便说点什么"}
