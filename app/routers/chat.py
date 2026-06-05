"""Chat endpoints。

- /chat       : 裸 LLM 流式，演示对比用
- /chat_rag   : 直连 health 知识库的 RAG，演示对比用
- /agent      : 完整动态路由（意图分类 → 5 路分支 → 流式回复 → ALERT 触发邮件）
"""
import asyncio
import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..db import get_recent_messages, save_message
from ..intent import classify_intent, derive_log_level
from ..llm import stream_chat
from ..notifier import send_alert
from ..retrieval import retrieve
from ..schemas import ChatRequest
from ..templates import (
    CHAT_SYSTEM,
    EMERGENCY_TEMPLATE,
    FRAUD_TEMPLATE,
    HEALTH_PROMPT,
    PSYCH_PROMPT_HIGH,
    PSYCH_PROMPT_LOW_MID,
    stream_template,
)
from ..vector_store import query  # 保留：/chat_rag 端点用纯向量做对比演示

router = APIRouter()


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


def _format_context(hits: list[dict]) -> str:
    if not hits:
        return "（无相关资料）"
    return "\n\n".join(f"[{i + 1}] {h['text']}" for i, h in enumerate(hits))


def _hits_preview(hits: list[dict]) -> list[dict]:
    """把检索结果序列化给前端，包含所有可用分数：
    distance(向量) / bm25_score / rrf_score / rerank_score

    text 不截断，长 chunk 在前端用 CSS max-height + 滚动呈现。
    """
    score_keys = ("distance", "bm25_score", "rrf_score", "rerank_score")
    out = []
    for h in hits:
        item = {"text": h["text"], "metadata": h.get("metadata", {})}
        for key in score_keys:
            if key in h and h[key] is not None:
                item[key] = round(float(h[key]), 4)
        out.append(item)
    return out


# ============ /chat：裸 LLM ============

@router.post("/chat")
async def chat(req: ChatRequest):
    history = get_recent_messages(req.session_id, n_turns=6)
    messages = (
        [{"role": "system", "content": CHAT_SYSTEM}]
        + history
        + [{"role": "user", "content": req.message}]
    )

    save_message(req.session_id, "user", req.message)

    async def event_gen():
        parts: list[str] = []
        async for delta in stream_chat(messages):
            parts.append(delta)
            yield _sse("message", {"delta": delta})
        full = "".join(parts)
        save_message(req.session_id, "assistant", full)
        yield _sse("done", {"answer": full})

    return EventSourceResponse(event_gen())


# ============ /chat_rag：直接走 health 集合 ============

@router.post("/chat_rag")
async def chat_rag(req: ChatRequest):
    hits = query("health", req.message, k=3)
    user_prompt = HEALTH_PROMPT.format(
        context=_format_context(hits), question=req.message
    )
    history = get_recent_messages(req.session_id, n_turns=6)
    messages = history + [{"role": "user", "content": user_prompt}]

    save_message(req.session_id, "user", req.message)

    async def event_gen():
        yield _sse("retrieved", {"hits": _hits_preview(hits)})
        parts: list[str] = []
        async for delta in stream_chat(messages):
            parts.append(delta)
            yield _sse("message", {"delta": delta})
        full = "".join(parts)
        save_message(req.session_id, "assistant", full)
        yield _sse("done", {"answer": full})

    return EventSourceResponse(event_gen())


# ============ /agent：完整动态路由 ============

@router.post("/agent")
async def agent(req: ChatRequest):
    """完整动态路由：先意图分类，再按意图分流到 5 种 handler。"""
    cls = await classify_intent(req.message)
    intent, risk = cls["intent"], cls["risk_level"]
    log_level = derive_log_level(intent, risk)

    save_message(
        req.session_id,
        "user",
        req.message,
        intent=intent,
        risk_level=risk,
        log_level=log_level,
    )

    # ALERT 级别派发后台任务发邮件，不阻塞流式响应
    if log_level == "ALERT":
        asyncio.create_task(
            send_alert(
                intent=intent,
                risk=risk,
                session_id=req.session_id,
                user_message=req.message,
            )
        )

    async def event_gen():
        # 先把分类结果推给前端，方便 UI 显示路由路径
        yield _sse(
            "intent",
            {"intent": intent, "risk_level": risk, "log_level": log_level},
        )

        parts: list[str] = []

        if intent == "CHAT":
            history = get_recent_messages(req.session_id, n_turns=6)
            messages = (
                [{"role": "system", "content": CHAT_SYSTEM}]
                + history
                + [{"role": "user", "content": req.message}]
            )
            async for delta in stream_chat(messages):
                parts.append(delta)
                yield _sse("message", {"delta": delta})

        elif intent == "HEALTH":
            hits = retrieve("health", req.message)
            yield _sse("retrieved", {"hits": _hits_preview(hits)})
            user_prompt = HEALTH_PROMPT.format(
                context=_format_context(hits), question=req.message
            )
            history = get_recent_messages(req.session_id, n_turns=6)
            messages = history + [{"role": "user", "content": user_prompt}]
            async for delta in stream_chat(messages):
                parts.append(delta)
                yield _sse("message", {"delta": delta})

        elif intent == "PSYCH":
            hits = retrieve("psych", req.message)
            yield _sse("retrieved", {"hits": _hits_preview(hits)})
            template = PSYCH_PROMPT_HIGH if risk == "high" else PSYCH_PROMPT_LOW_MID
            user_prompt = template.format(
                context=_format_context(hits), question=req.message
            )
            history = get_recent_messages(req.session_id, n_turns=6)
            messages = history + [{"role": "user", "content": user_prompt}]
            async for delta in stream_chat(messages):
                parts.append(delta)
                yield _sse("message", {"delta": delta})

        elif intent == "EMERGENCY":
            yield _sse("alert", {"reason": "EMERGENCY", "action": "已派发家属邮件预警"})
            async for delta in stream_template(EMERGENCY_TEMPLATE):
                parts.append(delta)
                yield _sse("message", {"delta": delta})

        elif intent == "FRAUD":
            yield _sse("alert", {"reason": "FRAUD", "action": "已派发家属邮件预警"})
            async for delta in stream_template(FRAUD_TEMPLATE):
                parts.append(delta)
                yield _sse("message", {"delta": delta})

        full = "".join(parts)
        save_message(
            req.session_id,
            "assistant",
            full,
            intent=intent,
            risk_level=risk,
            log_level=log_level,
        )
        yield _sse(
            "done",
            {
                "answer": full,
                "intent": intent,
                "risk_level": risk,
                "log_level": log_level,
            },
        )

    return EventSourceResponse(event_gen())
